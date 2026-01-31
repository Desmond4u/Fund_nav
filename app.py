import streamlit as st
import pandas as pd
import time
from datetime import datetime
import logging

from src.data_fetcher import get_fund_holdings, get_realtime_stock_prices, get_fund_history_nav
from src.valuation import estimate_nav_change

# Configure page
st.set_page_config(page_title="基金净值估算器", layout="wide")

st.title("🇨🇳 中国公募基金实时净值估算系统")
st.markdown("基于前十大重仓股实时估算基金净值涨跌幅。")

# Sidebar
st.sidebar.header("配置")
default_funds = "110011, 000001, 161725, 002611" # Examples: E-Fund Blue Chip, China AMC Growth, White Liquor, Gold
fund_input = st.sidebar.text_area("基金代码 (英文逗号分隔)", value=default_funds, height=100)
auto_refresh = st.sidebar.checkbox("自动刷新 (每60秒)", value=False)
refresh_btn = st.sidebar.button("立即刷新")

# Main Logic
from concurrent.futures import ThreadPoolExecutor, as_completed

@st.cache_data(ttl=3600)
def fetch_history_cached(code, days):
    return get_fund_history_nav(code, days)

def process_single_fund(code):
    """Background worker to fetch data for a single fund."""
    try:
        # 1. Fetch Holdings
        result_data = get_fund_holdings(code)
        
        if not result_data:
            return {
                '基金代码': code,
                '基金名称': '--',
                '持仓日期': '--',
                '状态': '获取持仓失败',
                '估算涨跌': None,
                '重仓股权重': None,
                'Details': []
            }
            
        # Unpack tuple
        if len(result_data) == 3:
             fund_name, holdings, report_date = result_data
        else:
             fund_name, holdings = result_data
             report_date = "--"
        
        # 2. Fetch Prices
        stock_fetch_codes = [h.get('fetch_code', h['code']) for h in holdings]
        prices = get_realtime_stock_prices(stock_fetch_codes)
        
        # 3. Estimate
        valuation = estimate_nav_change(holdings, prices)
        
        # 4. Fetch History (Last 365 days for flexibility)
        # Cached to avoid heavy network io
        history_df = fetch_history_cached(code, days=365)
        
        return {
            '基金代码': code,
            '基金名称': fund_name,
            '持仓日期': report_date,
            '状态': '成功',
            '估算涨跌': valuation['estimated_change'],
            '重仓股权重': valuation['total_weight_used'],
            'Details': valuation['details'],
            'History': history_df, # Add history
            '更新时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logging.error(f"Error processing {code}: {e}")
        return {
            '基金代码': code,
            '基金名称': 'Error',
            '持仓日期': '--',
            '状态': f'Error: {str(e)}',
            '估算涨跌': None,
            '重仓股权重': None,
            'Details': []
        }

def process_funds(code_list):
    results = []
    total = len(code_list)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    status_text.text("正在并发获取数据...")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        # Create map of future -> code (for ordering or debugging if needed, though we just wait for all)
        # To maintain order, we can map futures results back to list
        # Or just append as they complete. Dashboard typically lists in input order, so let's try to keeping order?
        # Actually simplest is map()
        
        futures_map = {executor.submit(process_single_fund, code.strip()): code.strip() for code in code_list if code.strip()}
        
        completed_count = 0
        
        # We want results in same order as input? Not strictly required but nice.
        # But as_completed yields out of order.
        # Let's collect all and then sort or assume order doesn't matter much (user can see by code).
        # Actually, let's just collect.
        
        for future in as_completed(futures_map):
            completed_count += 1
            progress_bar.progress(completed_count / len(futures_map))
            try:
                data = future.result()
                results.append(data)
            except Exception as e:
                logging.error(f"Future blocked: {e}")
                
    status_text.empty()
    progress_bar.empty()
    
    # Optional: Sort results to match input order
    # code_to_index = {code.strip(): i for i, code in enumerate(code_list) if code.strip()}
    # results.sort(key=lambda x: code_to_index.get(x['基金代码'], 999))
    
    return results

# Parse input
codes = [c.strip() for c in fund_input.split(',') if c.strip()]

if not codes:
    st.warning("请输入至少一个基金代码。")
    st.stop()

# Container for the dashboard
dashboard = st.empty()

def render_dashboard():
    with dashboard.container():
        data = process_funds(codes)
        
        if not data:
            st.error("未找到数据。")
            return

        # Summary Table
        summary_data = []
        for item in data:
            change_val = item['估算涨跌']
            weight_val = item['重仓股权重']
            
            summary_data.append({
                '基金代码': item['基金代码'],
                '基金名称': item['基金名称'],
                '持仓日期': item['持仓日期'],
                '估算涨跌': change_val, # Keep numeric for styling
                '重仓股权重': weight_val, # Keep numeric
                '状态': item['状态'],
                '更新时间': item.get('更新时间', '')
            })
            
            # Update Intraday History Logic (Restored)
            if item['状态'] == '成功' and item['估算涨跌'] is not None:
                f_code = item['基金代码']
                if 'fund_intraday' not in st.session_state:
                    st.session_state['fund_intraday'] = {}
                
                if f_code not in st.session_state['fund_intraday']:
                    st.session_state['fund_intraday'][f_code] = pd.DataFrame(columns=['Time', 'Estimate'])
                
                current_time = datetime.now().strftime("%H:%M")
                
                # Simple append
                new_row = pd.DataFrame({'Time': [current_time], 'Estimate': [item['估算涨跌']]})
                st.session_state['fund_intraday'][f_code] = pd.concat([st.session_state['fund_intraday'][f_code], new_row], ignore_index=True)

        df_summary = pd.DataFrame(summary_data)
        
        # Style the dataframe
        def color_change(val):
            if isinstance(val, (float, int)):
                if val > 0:
                    return 'color: #D32F2F' # Red
                elif val < 0:
                    return 'color: #388E3C' # Green
            return ''

        # Display Summary
        st.subheader("概览")
        
        # Create a display copy
        df_display = df_summary[['基金代码', '基金名称', '持仓日期', '估算涨跌', '重仓股权重', '状态', '更新时间']]
        
        # Apply Styler
        styler = df_display.style\
            .format({'估算涨跌': "{:+.2f}%", '重仓股权重': "{:.2f}%"}, na_rep="--")\
            .map(color_change, subset=['估算涨跌'])
            
        st.dataframe(styler, use_container_width=True)
        
        # Detail Expander
        st.subheader("详细信息")
        tabs = st.tabs([f"{d['基金代码']}" for d in data])
        
        for i, tab in enumerate(tabs):
            with tab:
                item = data[i]
                if item['状态'] == '成功':
                    # --- Metrics Row ---
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        st.metric("实时估算涨跌", f"{item['估算涨跌']:+.2f}%", delta=None)
                    with c2:
                         st.metric("前十大持仓占比", f"{item['重仓股权重']:.2f}%")
                    with c3:
                         st.metric("持仓报告期", item['持仓日期'])
                    
                    st.divider()
                    
                    # --- Charts Area (Tabs) ---
                    chart_tab1, chart_tab2 = st.tabs(["📉 实时分时走势", "📅 历史净值趋势"])
                    
                    with chart_tab1:
                         # Intraday Chart
                         f_code = item['基金代码']
                         if 'fund_intraday' in st.session_state and f_code in st.session_state['fund_intraday']:
                             df_intra = st.session_state['fund_intraday'][f_code]
                             if not df_intra.empty:
                                 # Use Altair for consistency
                                 import altair as alt
                                 chart_intra = alt.Chart(df_intra).mark_line(color='#FFA500').encode(
                                     x=alt.X('Time', title='时间'),
                                     y=alt.Y('Estimate', title='估算涨跌(%)', scale=alt.Scale(zero=False))
                                 ).properties(height=250)
                                 st.altair_chart(chart_intra, use_container_width=True)
                             else:
                                 st.info("暂无今日实时数据，请等待刷新...")
                         else:
                             st.info("数据收集中...")
                    
                    with chart_tab2:
                        # Historical Chart
                        if 'History' in item and item['History'] is not None and not item['History'].empty:
                            # Date Range Selector
                            range_map = {'1周': 7, '1月': 30, '3月': 90, '6月': 180, '1年': 365}
                            selected_range = st.radio(
                                "时间范围", 
                                list(range_map.keys()), 
                                index=1, 
                                key=f"range_{item['基金代码']}",
                                horizontal=True,
                                label_visibility="collapsed"
                            )
                            
                            days_limit = range_map[selected_range]
                            hist_df = item['History']
                            
                            # Filter
                            start_date = pd.Timestamp.now() - pd.Timedelta(days=days_limit)
                            chart_df = hist_df[hist_df['date'] >= start_date]
                            
                            import altair as alt
                            chart_hist = alt.Chart(chart_df).mark_line().encode(
                                x=alt.X('date', title='日期', axis=alt.Axis(format='%m-%d')),
                                y=alt.Y('nav', title='单位净值', scale=alt.Scale(zero=False)),
                                tooltip=['date', 'nav']
                            ).properties(height=250)
                            st.altair_chart(chart_hist, use_container_width=True)
                        else:
                            st.warning("暂无历史数据")

                    st.caption("注意：估值仅基于已披露的前十大重仓股，并已归一化处理。")
                    
                    # --- Holdings Table ---
                    with st.expander("查看重仓股详情", expanded=False):
                        details = item['Details']
                        df_det = pd.DataFrame(details)
                        
                        if not df_det.empty:
                            df_det = df_det[['code', 'name', 'weight', 'price', 'change']]
                            df_det.columns = ['代码', '名称', '权重(%)', '现价', '涨跌(%)']
                            
                            # Style highlights
                            def highlight_change(val):
                                color = '#d63031' if val > 0 else '#00b894' if val < 0 else ''
                                return f'color: {color}'
                                
                            st.dataframe(
                                df_det.style.map(highlight_change, subset=['涨跌(%)'])
                                            .format({'权重(%)': "{:.2f}", '现价': "{:.2f}", '涨跌(%)': "{:+.2f}"}),
                                use_container_width=True
                            )
                        else:
                            st.info("暂无持仓详情。")
                else:
                    st.error(f"获取数据失败: {item.get('状态', 'Unknown Error')}")

# Main Loop Logic
if auto_refresh:
    while True:
        render_dashboard()
        time.sleep(60)
        st.rerun()
else:
    render_dashboard()

if refresh_btn:
    st.rerun()
