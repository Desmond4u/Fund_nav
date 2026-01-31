from typing import List, Dict, Optional
import logging

def estimate_nav_change(holdings: List[Dict], prices: Dict[str, Dict]) -> Dict:
    """
    Estimates the real-time NAV change based on holdings and current stock prices.
    
    Args:
        holdings: List of dicts, each having {'code', 'weight', ...}
        prices: Dict of {code: {'change': float, ...}}
        
    Returns:
        Dict: {
            'estimated_change': float, # The estimated percentage change (e.g., 1.5 for +1.5%)
            'total_weight_used': float, # The sum of weights of stocks used for calculation
            'details': List[Dict] # Details for UI
        }
    """
    if not holdings:
        return {'estimated_change': 0.0, 'total_weight_used': 0.0, 'details': []}
        
    total_weighted_change = 0.0
    total_weight = 0.0
    details = []
    
    for item in holdings:
        code = item['code']
        # Use fetch_code for lookup if available (for HK/US stocks), else fallback to display code
        lookup_code = item.get('fetch_code', code)
        
        weight = item.get('weight', 0.0)
        
        price_info = prices.get(lookup_code)
        
        if price_info:
            change = price_info.get('change', 0.0)
            current_price = price_info.get('price', 0.0)
            name = price_info.get('name', item.get('name', 'Unknown'))
            
            weighted_change = change * weight
            total_weighted_change += weighted_change
            total_weight += weight
            
            details.append({
                'code': code,
                'name': name,
                'weight': weight,
                'price': current_price,
                'change': change,
                'contribution': weighted_change # Contribution to the sum, logic-wise
            })
        else:
            # Stock price not found (e.g. HK stock or fetching failed)
            details.append({
                'code': code,
                'name': item.get('name', 'Unknown'),
                'weight': weight,
                'price': None,
                'change': None,
                'contribution': 0.0
            })

    if total_weight == 0:
        return {'estimated_change': 0.0, 'total_weight_used': 0.0, 'details': details}
        
    # Normalized Estimate
    # Formula: Sum(Weight * Change) / Sum(Weights)
    # Assumption: The unsampled portion of the fund behaves like the sampled top 10.
    final_estimate = total_weighted_change / total_weight
    
    return {
        'estimated_change': final_estimate,
        'total_weight_used': total_weight,
        'details': details
    }
