import pandas as pd
import numpy as np

class UnitEconomicsEngine:
    """
    Core calculation engine for Landed Cost and Walmart Unit Economics.
    Uses vectorized pandas operations for performance on large datasets.
    """

    def __init__(self, df: pd.DataFrame, mappings: dict, assumptions: dict):
        self.df = df.copy()
        self.map = mappings
        self.assumptions = assumptions
        self.logs = []

    def log(self, message):
        self.logs.append(message)

    def _safe_numeric(self, col_name, default=0.0):
        """Ensures a column exists and is numeric."""
        if col_name not in self.df.columns:
            self.df[col_name] = default
        self.df[col_name] = pd.to_numeric(self.df[col_name], errors='coerce').fillna(default)

    def run_conversions(self):
        """Normalize units (cm to in, kg to lb, etc.)"""
        # Map input columns to standard internal names
        target_cols = {
            self.map.get('sku', 'sku'): 'sku',  # <--- FIXED: Added missing SKU mapping
            self.map.get('qty', 'qty'): 'qty',
            self.map.get('unit_cost', 'unit_cost'): 'unit_cost',
            self.map.get('length', 'length'): 'length',
            self.map.get('width', 'width'): 'width',
            self.map.get('height', 'height'): 'height',
            self.map.get('weight', 'weight'): 'weight',
            self.map.get('selling_price', 'selling_price'): 'selling_price',
            self.map.get('duty_rate', 'duty_rate'): 'duty_rate_pct'
        }
        
        # Rename valid columns, fill missing optional ones
        for user_col, internal_col in target_cols.items():
            if user_col and user_col in self.df.columns:
                self.df[internal_col] = self.df[user_col]
            else:
                # Handle defaults
                if internal_col == 'sku':
                    self.df[internal_col] = 'Unknown-SKU'
                else:
                    self.df[internal_col] = 0.0

        # Enforce numeric
        for col in ['qty', 'unit_cost', 'length', 'width', 'height', 'weight', 'selling_price', 'duty_rate_pct']:
            self._safe_numeric(col)

        # 1. Dimensional Conversions to METERS (for CBM) and INCHES (for WFS)
        uom_dim = self.assumptions.get('uom_dim', 'cm') # 'cm' or 'in'
        
        if uom_dim == 'in':
            self.df['len_m'] = self.df['length'] * 0.0254
            self.df['wid_m'] = self.df['width'] * 0.0254
            self.df['hgt_m'] = self.df['height'] * 0.0254
            self.df['len_in'] = self.df['length']
            self.df['wid_in'] = self.df['width']
            self.df['hgt_in'] = self.df['height']
        else: # cm
            self.df['len_m'] = self.df['length'] / 100
            self.df['wid_m'] = self.df['width'] / 100
            self.df['hgt_m'] = self.df['height'] / 100
            self.df['len_in'] = self.df['length'] / 2.54
            self.df['wid_in'] = self.df['width'] / 2.54
            self.df['hgt_in'] = self.df['height'] / 2.54

        # 2. Weight Conversions to KG (for Freight) and LB (for WFS)
        uom_weight = self.assumptions.get('uom_weight', 'kg') # 'kg' or 'lb'
        
        if uom_weight == 'lb':
            self.df['weight_kg'] = self.df['weight'] * 0.453592
            self.df['weight_lb'] = self.df['weight']
        else: # kg
            self.df['weight_kg'] = self.df['weight']
            self.df['weight_lb'] = self.df['weight'] * 2.20462

        # 3. Volume Metrics
        self.df['unit_cbm'] = self.df['len_m'] * self.df['wid_m'] * self.df['hgt_m']
        self.df['unit_cuft'] = self.df['unit_cbm'] * 35.3147
        self.df['total_line_cbm'] = self.df['unit_cbm'] * self.df['qty']
        self.df['total_line_weight_kg'] = self.df['weight_kg'] * self.df['qty']
        
        # 4. Dimensional Weight (Volumetric) - Standard divisor 139 for lb/in usually
        dim_div = self.assumptions.get('dim_divisor', 139) 
        self.df['dim_weight_lb'] = (self.df['len_in'] * self.df['wid_in'] * self.df['hgt_in']) / dim_div
        self.df['billable_weight_lb'] = self.df[['weight_lb', 'dim_weight_lb']].max(axis=1)

    def calculate_landed_cost(self):
        """Calculates freight, duty, and fees."""
        
        # --- A. Purchase Cost ---
        # Convert currency if needed
        fx_rate = self.assumptions.get('fx_rate', 1.0) # e.g. 7.1 RMB to USD -> input 1/7.1 if cost is RMB
        self.df['unit_cost_usd'] = self.df['unit_cost'] * fx_rate
        self.df['total_purchase_cost'] = self.df['unit_cost_usd'] * self.df['qty']

        # --- B. Freight Allocation ---
        # 1. Calculate Total Shipment Volume/Weight
        total_cbm = self.df['total_line_cbm'].sum()
        total_kg = self.df['total_line_weight_kg'].sum()
        
        if total_cbm == 0: total_cbm = 1 # Avoid div/0
        if total_kg == 0: total_kg = 1

        # 2. Total Freight Bill (Ocean + Origin + Destination + Trucking)
        # Note: If FCL, user inputs total container cost. If LCL, user might input rate/cbm.
        # We simplify: User inputs a "Total Freight & Logistics Cost" for the batch, OR we compute it.
        # Here we assume the input in assumptions is the TOTAL bill for this batch/container.
        freight_total_spend = self.assumptions.get('freight_total_spend', 0.0)

        # 3. Allocation Shares
        alloc_method = self.assumptions.get('allocation_method', 'cbm') # cbm, weight, hybrid
        
        if alloc_method == 'weight':
            self.df['alloc_share'] = self.df['total_line_weight_kg'] / total_kg
        elif alloc_method == 'hybrid':
            self.df['alloc_share'] = 0.5 * (self.df['total_line_cbm'] / total_cbm) + \
                                     0.5 * (self.df['total_line_weight_kg'] / total_kg)
        else: # Default cbm
            self.df['alloc_share'] = self.df['total_line_cbm'] / total_cbm

        self.df['allocated_freight_total'] = freight_total_spend * self.df['alloc_share']
        self.df['unit_freight_cost'] = self.df['allocated_freight_total'] / self.df['qty']

        # --- C. Duties & Customs ---
        # Duty is specific to the line item
        self.df['unit_duty_amt'] = self.df['unit_cost_usd'] * (self.df['duty_rate_pct'] / 100.0)
        
        # MPF / HMF / Bond / Broker (Fixed costs for entry)
        # MPF is complex (ad valorem with min/max). We calculate total entry MPF then allocate.
        mpf_rate = self.assumptions.get('mpf_rate', 0.003464)
        mpf_min = self.assumptions.get('mpf_min', 31.0)
        mpf_max = self.assumptions.get('mpf_max', 614.0)
        hmf_rate = self.assumptions.get('hmf_rate', 0.00125) # Ocean only
        fixed_brokerage = self.assumptions.get('brokerage_fee', 0.0)
        
        # Calculate theoretical MPF on total invoice
        total_invoice_val = self.df['total_purchase_cost'].sum()
        total_mpf = min(max(total_invoice_val * mpf_rate, mpf_min), mpf_max)
        total_hmf = total_invoice_val * hmf_rate
        total_fixed_import_fees = total_mpf + total_hmf + fixed_brokerage
        
        # Allocate fixed import fees based on value share (standard practice)
        # or reuse the freight allocation method. Let's use value share for duties/fees.
        if total_invoice_val == 0: total_invoice_val = 1
        self.df['value_share'] = self.df['total_purchase_cost'] / total_invoice_val
        self.df['allocated_import_fees'] = total_fixed_import_fees * self.df['value_share']
        self.df['unit_import_fees'] = self.df['allocated_import_fees'] / self.df['qty']

        # --- D. Total Landed Cost ---
        self.df['landed_cost_unit'] = (
            self.df['unit_cost_usd'] + 
            self.df['unit_freight_cost'] + 
            self.df['unit_duty_amt'] + 
            self.df['unit_import_fees']
        )

    def calculate_walmart_economics(self):
        """Calculates WFS fees, referral fees, and profit margins."""
        
        # 1. Referral Fee
        def_ref_rate = self.assumptions.get('default_referral_pct', 15.0)
        # Check if mapped column exists for override, else use default
        if 'walmart_referral_pct' in self.df.columns and self.df['walmart_referral_pct'].sum() > 0:
             self.df['referral_fee'] = self.df['selling_price'] * (self.df['walmart_referral_pct'] / 100.0)
        else:
             self.df['referral_fee'] = self.df['selling_price'] * (def_ref_rate / 100.0)

        # 2. WFS Fulfillment Fee
        # Simplified Logic: Base Fee + (Weight - Base Weight) * Excess Rate
        # In a full production app, this would merge with a CSV lookup table.
        # We will use assumptions for a simple linear model or tiered model.
        
        wfs_base = self.assumptions.get('wfs_base_fee', 3.45) # e.g., small standard
        wfs_weight_allowance = self.assumptions.get('wfs_base_weight_lb', 1.0)
        wfs_excess_rate = self.assumptions.get('wfs_excess_per_lb', 0.40)
        
        # Basic logical calculation (User can override with a lookup table in V2)
        def calc_wfs(row):
            bw = row['billable_weight_lb']
            if bw <= wfs_weight_allowance:
                return wfs_base
            else:
                return wfs_base + ((bw - wfs_weight_allowance) * wfs_excess_rate)
        
        self.df['wfs_fulfillment_fee'] = self.df.apply(calc_wfs, axis=1)

        # 3. WFS Storage
        # Monthly cost based on cubic feet
        storage_rate = self.assumptions.get('wfs_storage_rate', 0.87) # Jan-Sept rate
        self.df['wfs_storage_fee_mo'] = self.df['unit_cuft'] * storage_rate

        # 4. Other costs (Ads, Returns)
        ads_pct = self.assumptions.get('ads_pct_sales', 5.0)
        returns_pct = self.assumptions.get('returns_pct_sales', 3.0)
        
        self.df['ads_cost'] = self.df['selling_price'] * (ads_pct / 100.0)
        self.df['returns_cost'] = self.df['selling_price'] * (returns_pct / 100.0)

        # 5. Profitability
        self.df['total_amz_fees'] = (
            self.df['referral_fee'] + 
            self.df['wfs_fulfillment_fee'] + 
            self.df['wfs_storage_fee_mo'] + 
            self.df['ads_cost'] + 
            self.df['returns_cost']
        )
        
        self.df['net_profit'] = self.df['selling_price'] - self.df['landed_cost_unit'] - self.df['total_amz_fees']
        self.df['net_margin_pct'] = (self.df['net_profit'] / self.df['selling_price']) * 100.0
        self.df['roi_pct'] = (self.df['net_profit'] / self.df['landed_cost_unit']) * 100.0
        
        # Breakeven Price (Cost + Fixed Fees) / (1 - % Fees)
        # Variable % fees = referral + ads + returns
        var_fee_pct = (def_ref_rate + ads_pct + returns_pct) / 100.0
        fixed_costs = self.df['landed_cost_unit'] + self.df['wfs_fulfillment_fee'] + self.df['wfs_storage_fee_mo']
        
        self.df['breakeven_price'] = fixed_costs / (1 - var_fee_pct)
        
        # Flagging
        self.df['is_profitable'] = self.df['net_profit'] > 0

    def get_results(self):
        return self.df