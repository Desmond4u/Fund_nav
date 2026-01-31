import requests
import re
import json
import logging
import pandas as pd
from io import StringIO
from typing import Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_fund_history_nav(fund_code: str, days: int = 365) -> Optional[pd.DataFrame]:
    """
    Fetches historical NAV data for the fund (parallel paging).
    Returns DataFrame with columns ['date', 'nav'].
    """
    # Each page has 20 items. 
    # 365 days / 20 = ~19 pages. Safe to fetch 20 pages for 1 year.
    # To cover non-trading days, 365 calendar days is approx 250 trading days (13 pages).
    # 20 pages covers ~400 items, enough for > 1.5 years.
    
    max_pages = (days // 20) + 2
    
    url = "http://api.fund.eastmoney.com/f10/lsjz"
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': f'http://fundf10.eastmoney.com/jjjz_{fund_code}.html'
    }
    
    data_list = []
    
    def fetch_page(page):
        try:
            params = {
                'fundCode': fund_code,
                'pageIndex': page,
                'pageSize': 20,
            }
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            # Response is JSON
            data = resp.json()
            if 'Data' in data and data['Data'] and 'LSJZList' in data['Data']:
                return data['Data']['LSJZList']
        except Exception as e:
            logging.warning(f"Error fetching page {page} for {fund_code}: {e}")
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    # Use internal executor
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_page, p) for p in range(1, max_pages + 1)]
        for f in as_completed(futures):
            res = f.result()
            if res:
                data_list.extend(res)
    
    if not data_list:
        return None
        
    try:
        # Convert to DF
        df = pd.DataFrame(data_list)
        # Columns: FSRQ (Date), DWJZ (Nav)
        if 'FSRQ' in df.columns and 'DWJZ' in df.columns:
            df = df[['FSRQ', 'DWJZ']].copy()
            df.columns = ['date', 'nav']
            df['date'] = pd.to_datetime(df['date'])
            df['nav'] = pd.to_numeric(df['nav'], errors='coerce')
            df.sort_values('date', inplace=True)
            
            # Filter by date limit locally to be precise
            start_date = pd.Timestamp.now() - pd.Timedelta(days=days)
            df = df[df['date'] >= start_date]
            
            return df
    except Exception as e:
        logging.error(f"Error processing history for {fund_code}: {e}")
        
    return None

def _get_fund_name_backup(fund_code: str) -> Optional[str]:
    """
    Tries to get fund name from other pages (e.g. zqcc) if jjcc is empty.
    """
    url = "http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    params = {'type': 'zqcc', 'code': fund_code, 'topline': 10}
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': f'http://fundf10.eastmoney.com/ccmx_{fund_code}.html'
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=3)
        # Match <a href='...'>Name</a>
        match = re.search(r"fund.eastmoney.com/\d+.html'>(.*?)</a>", resp.text)
        if match:
            return match.group(1)
        # Fallback for title='...'
        match = re.search(r"title='(.*?)'", resp.text)
        if match:
            return match.group(1)
    except:
        pass
    return None

def _search_etf_code(etf_name: str) -> Optional[str]:
    """
    Searches for an ETF code by name using Sina Suggest.
    """
    try:
        url = f"http://suggest3.sinajs.cn/suggest/type=&key={etf_name}"
        resp = requests.get(url, timeout=3)
        content = resp.content.decode('gbk', errors='ignore')
        # Format: var suggestvalue="Name,Count,Code,...;..."
        if 'suggestvalue="' in content:
            val = content.split('suggestvalue="')[1].strip('";')
            if not val:
                return None
            first_match = val.split(';')[0]
            parts = first_match.split(',')
            if len(parts) >= 4:
                return parts[2]
    except Exception as e:
        logging.warning(f"Search failed for {etf_name}: {e}")
    return None

def get_fund_holdings(fund_code: str) -> Optional[Tuple[str, List[Dict[str, float]], str]]:
    """
    Fetches the top 10 heavy holdings for a given fund code from EastMoney.
    If it's an ETF Feeder, tries to find the target ETF.
    
    Returns:
        tuple: (fund_name, holdings_list, report_date_str) or None
    """
    # 1. Try Stocks (jjcc)
    url = "http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    params = {
        'type': 'jjcc',   
        'code': fund_code, 
        'topline': 10,
        'year': '',
        'month': '',
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': f'http://fundf10.eastmoney.com/ccmx_{fund_code}.html'
    }

    fund_name = None
    report_date = "--"
    holdings = []
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        content = response.text
        
        # Try to extract Name first from the HTML snippet inside content
        name_match = re.search(r"title='(.*?)'", content)
        if name_match:
            fund_name = name_match.group(1)
            
        # Try to extract report date: 截止至：<font class='px12'>2025-12-31</font>
        # or similar
        date_match = re.search(r"截止至：<font class='px12'>(.*?)</font>", content)
        if date_match:
             report_date = date_match.group(1)
        
        # Parse Content
        match = re.search(r'content:"(.*?)",\s*\w+\s*[:=]', content, re.DOTALL)
        html_table = ""
        
        if match:
            html_table = match.group(1)
        else:
             try:
                part1 = content.split('content:"')[1]
                html_table = part1.split('",')[0]
             except:
                pass

        has_data = False
        if html_table and "暂无数据" not in html_table and len(html_table) > 50:
             # Parse Table with Regex to capture Links/Market IDs
             # Pattern for rows
             rows = re.findall(r"<tr>(.*?)</tr>", html_table, re.DOTALL)
             
             for row_html in rows:
                 # Skip header
                 if "th" in row_html: continue
                 
                 try:
                     # 1. Extract Code and Market from Link
                     # href='//quote.eastmoney.com/unify/r/116.00700'
                     link_match = re.search(r"unify/r/(\d+)\.([a-zA-Z0-9]+)", row_html)
                     
                     stock_code = "Unknown"
                     market_id = None
                     
                     if link_match:
                         market_id = link_match.group(1)
                         stock_code = link_match.group(2)
                     else:
                         # Fallback to cell text if link not standard
                         # <td class='toc'>00700</td>
                         # Try to find the second column
                         cols = re.findall(r"<td.*?>(.*?)</td>", row_html, re.DOTALL)
                         if len(cols) > 1:
                             # Strip tags
                             stock_code = re.sub(r"<.*?>", "", cols[1]).strip()
                     
                     # Extract Name (3rd col)
                     cols = re.findall(r"<td.*?>(.*?)</td>", row_html, re.DOTALL)
                     if len(cols) < 7: continue
                     
                     stock_name = re.sub(r"<.*?>", "", cols[2]).strip()
                     
                     # Extract Weight (7th col, index 6)
                     weight_str = re.sub(r"<.*?>", "", cols[6]).strip().replace('%', '')
                     if not weight_str or weight_str == '--': continue
                     
                     weight = float(weight_str)
                     
                     # Generate Sina Fetch Code
                     sina_code = None
                     
                     if market_id:
                         mid = int(market_id)
                         if mid == 0: # SZ
                             sina_code = f"sz{stock_code}"
                         elif mid == 1: # SH
                             sina_code = f"sh{stock_code}"
                         elif mid == 116: # HK
                             # Pad HK code to 5 digits for Sina
                             # EastMoney might give '700', '00700'. Sina needs '00700'.
                             sina_code = f"rt_hk{stock_code.zfill(5)}"
                         elif mid >= 100: # US (105, 106, 107...)
                             sina_code = f"gb_{stock_code.lower()}"
                         else:
                             # Default A-share fallback if ID known or new
                             if stock_code.startswith('6') or stock_code.startswith('9'): sina_code = f"sh{stock_code}"
                             else: sina_code = f"sz{stock_code}"
                     
                     else:
                         # Fallback logic if no link found
                         # Guess based on format
                         if re.search(r'[a-zA-Z]', stock_code): sina_code = f"gb_{stock_code.lower()}"
                         elif len(stock_code) < 6: sina_code = f"rt_hk{stock_code.zfill(5)}"
                         else: 
                             # Assume A-share
                             if stock_code.startswith('6') or stock_code.startswith('5'): sina_code = f"sh{stock_code}"
                             elif stock_code.startswith('4') or stock_code.startswith('8'): sina_code = f"bj{stock_code}"
                             else: sina_code = f"sz{stock_code}"
                     
                     holdings.append({
                         'code': stock_code, # Display Code
                         'name': stock_name,
                         'weight': weight,
                         'fetch_code': sina_code # API Code
                     })
                     has_data = True
                     
                 except Exception as e:
                     logging.warning(f"Error parsing row: {e}")
                     continue
        
        # Determine if we should look for ETF Target
        # Criteria:
        # 1. No holdings found
        # 2. OR holdings found but total weight is suspicious (e.g. < 60%) AND name contains "ETF" or "联接" (Feeder)
        
        total_weight = sum(h['weight'] for h in holdings)
        is_suspicious_low_weight = total_weight < 60.0 # Strict check
        
        # If fund_name is missing, try backup (backup usually doesn't have date easily, or we can fetch again, but name is enough)
        if not fund_name:
            fund_name = _get_fund_name_backup(fund_code)
            
        is_feeder_named = fund_name and ("联接" in fund_name or "ETF" in fund_name)
        
        if not holdings or (is_suspicious_low_weight and is_feeder_named):
            if is_feeder_named:
                # Heuristic for Feeder
                logging.info(f"Fund {fund_code} ({fund_name}) seems to be a Feeder (Weight: {total_weight}%). Trying to find target...")
                
                # Clean name
                target_name = fund_name
                target_name = re.sub(r"联接[A-Z]?$", "", target_name) # Remove trail
                target_name = re.sub(r"联接", "", target_name) # Remove anywhere
                target_name = target_name.replace("发起式", "")

                # Search
                logging.info(f"Searching for target: {target_name}")
                target_code = _search_etf_code(target_name)
                
                if target_code == fund_code:
                     target_code = None
                
                if target_code:
                    logging.info(f"Found target ETF: {target_code}")
                    # Use 'Real-time' as date for ETF tracking since it IS real-time
                    # Feeder target is A-share ETF, assumed to be SZ/SH. simple logic:
                    # ETF usually starts with 159 (sz) or 51 (sh).
                    # Actually get_realtime_stock_prices handles simple codes.
                    # But we updated holding structure.
                    # We need to fabricate the fetch_code for ETF target.
                    etf_fetch_code = target_code
                    if target_code.startswith('5'): etf_fetch_code = f"sh{target_code}"
                    else: etf_fetch_code = f"sz{target_code}"
                    
                    return (fund_name, [{'code': target_code, 'name': target_name, 'weight': 95.0, 'fetch_code': etf_fetch_code}], "实时追踪")

                # Try searching with "ETF" appended
                if "ETF" not in target_name:
                     target_code = _search_etf_code(target_name + "ETF")
                     if target_code and target_code != fund_code:
                         etf_fetch_code = target_code
                         if target_code.startswith('5'): etf_fetch_code = f"sh{target_code}"
                         else: etf_fetch_code = f"sz{target_code}"
                         
                         return (fund_name, [{'code': target_code, 'name': target_name+"ETF", 'weight': 95.0, 'fetch_code': etf_fetch_code}], "实时追踪")
        
        if holdings:
            return (fund_name if fund_name else f"Fund {fund_code}", holdings, report_date)

        return None

    except Exception as e:
        logging.error(f"Error fetching holdings for {fund_code}: {e}")
        return None

def get_realtime_stock_prices(stock_codes: List[str]) -> Dict[str, Dict]:
    """
    Fetches real-time stock prices from Sina Finance.
    Accepts specific Sina codes (e.g. sh600519, rt_hk00700, gb_aapl).
    """
    if not stock_codes:
        return {}
    
    unique_codes = list(set(stock_codes))
    results = {}
    
    # Helper to chunk list
    def chunked(l, n):
        for i in range(0, len(l), n):
            yield l[i:i + n]
            
    headers = {'Referer': 'http://finance.sina.com.cn/'}
    
    for batch in chunked(unique_codes, 20):
        list_param = ",".join(batch)
        url = f"http://hq.sinajs.cn/list={list_param}"
        
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            content = resp.content.decode('gbk', errors='ignore')
            
            for line in content.strip().splitlines():
                if not line or '=""' in line: continue
                
                try:
                    parts = line.split('=')
                    if len(parts) < 2: continue
                    
                    key = parts[0].strip().split('hq_str_')[-1]
                    data_str = parts[1].strip('"')
                    if not data_str: continue
                    data = data_str.split(',')
                    
                    name = "Unknown"
                    price = 0.0
                    change_pct = 0.0
                    
                    # Determine Parser by Key Prefix
                    if key.startswith('rt_hk'): # HK
                        if len(data) >= 9:
                            name = data[1] # Chinese Name
                            price = float(data[6])
                            change_pct = float(data[8])
                    
                    elif key.startswith('gb_'): # US
                        if len(data) >= 3:
                            name = data[0]
                            price = float(data[1])
                            change_pct = float(data[2])
                    
                    else: # A-Share (sh/sz/bj)
                        if len(data) >= 4:
                            name = data[0]
                            pre_close = float(data[2])
                            current_price = float(data[3])
                            price = current_price
                            
                            if pre_close > 0:
                                change_pct = ((current_price - pre_close) / pre_close) * 100
                            else:
                                change_pct = 0.0
                    
                    results[key] = {
                        'name': name,
                        'price': price,
                        'change': change_pct
                    }
                    
                except Exception as e:
                    logging.warning(f"Failed to parse line for {key if 'key' in locals() else 'unknown'}: {e}")
                    continue
        except Exception as e:
             logging.error(f"Error fetching batch prices: {e}")
             
    return results
