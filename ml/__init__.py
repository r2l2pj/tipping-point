"""Tipping Point predictive layer — LHASA-v2-style learned landslide trigger.

XGBoost on GPM IMERG rain, SMAP L4 wetness and NASADEM/MODIS terrain,
trained on the RS-2024 post-disaster inventories (UFRGS, Cemaden; CC-BY-4.0).
Spec: .crew/reports/ml-predict.md §5.
"""
