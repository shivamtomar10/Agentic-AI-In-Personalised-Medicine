"""ML model predictor: loads trained scikit-learn models and predicts risk from patient state.

At inference time, we take the canonicalized patient state (after PatientDataAgent has run)
and map the lab values and vitals into the feature space expected by the trained models.
Missing values are handled by the models' built-in SimpleImputer.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, Optional, Tuple

import joblib
import pandas as pd

from src.contracts.patient import PatientState
from src.ml.trainer import (
    CKD_MODEL_PATH, DIABETES_MODEL_PATH,
    CKD_META_PATH, DIABETES_META_PATH,
    models_exist
)

logger = logging.getLogger(__name__)

class MLPredictor:
    def __init__(self):
        self._ckd_model = None
        self._diabetes_model = None
        self._ckd_meta = {}
        self._diabetes_meta = {}
        self._loaded = False
        
    def load(self) -> bool:
        """Load models and metadata from disk."""
        if self._loaded:
            return True
            
        if not models_exist():
            logger.warning("ML models not found on disk. Run `python src/ml/trainer.py` or `python run.py train-models`.")
            return False
            
        try:
            self._ckd_model = joblib.load(CKD_MODEL_PATH)
            self._diabetes_model = joblib.load(DIABETES_MODEL_PATH)
            self._ckd_meta = json.loads(CKD_META_PATH.read_text(encoding="utf-8"))
            self._diabetes_meta = json.loads(DIABETES_META_PATH.read_text(encoding="utf-8"))
            self._loaded = True
            return True
        except Exception as e:
            logger.error(f"Failed to load ML models: {e}")
            return False

    def predict_ckd_risk(self, state: PatientState) -> Optional[Dict[str, Any]]:
        """Predict probability of CKD based on patient state."""
        if not self.load() or not self._ckd_model:
            return None
            
        # Extract features matching the CKD model's expectations
        # Expected features: age, blood_pressure, serum_creatinine, blood_glucose_random,
        # haemoglobin, albumin, blood_urea, packed_cell_volume, hypertension, diabetes_mellitus
        
        # We need at least one lab value to make a meaningful prediction
        if not state.latest_labs:
            return None
            
        # Map state data to feature dictionary
        features = {}
        
        # Demographics
        features["age"] = state.demographics.age if state.demographics.age is not None else float('nan')
        
        # Vitals
        features["blood_pressure"] = state.vitals.get("systolic_bp").value if "systolic_bp" in state.vitals else float('nan')
        
        # Labs
        labs = state.latest_labs
        features["serum_creatinine"] = labs.get("creatinine").value if "creatinine" in labs else float('nan')
        features["blood_glucose_random"] = labs.get("glucose").value if "glucose" in labs else float('nan')
        features["haemoglobin"] = labs.get("hemoglobin").value if "hemoglobin" in labs else float('nan')
        features["albumin"] = labs.get("albumin").value if "albumin" in labs else float('nan')
        features["blood_urea"] = labs.get("bun").value if "bun" in labs else float('nan')
        features["packed_cell_volume"] = labs.get("hematocrit").value if "hematocrit" in labs else float('nan')
        
        # Conditions
        conds = [c.code.lower() + c.display_name.lower() for c in state.active_conditions]
        features["hypertension"] = 1.0 if any("hypertension" in c or "i10" in c for c in conds) else 0.0
        features["diabetes_mellitus"] = 1.0 if any("diabetes" in c or "e11" in c for c in conds) else 0.0

        # If we have very little data, skip prediction to avoid over-reliance on imputation
        non_nan_count = sum(1 for v in features.values() if pd.notna(v))
        if non_nan_count < 3:
            return None
            
        # Create DataFrame matching expected column order
        df = pd.DataFrame([features])
        
        try:
            # Get probability of class 1 (CKD)
            prob = self._ckd_model.predict_proba(df)[0][1]
            return {
                "risk_score": float(prob),
                "model": self._ckd_meta.get("model", "RandomForest"),
                "dataset": self._ckd_meta.get("dataset", "UCI CKD"),
                "features_used": non_nan_count,
                "top_features": list(self._ckd_meta.get("feature_importances", {}).keys())[:3]
            }
        except Exception as e:
            logger.error(f"CKD prediction failed: {e}")
            return None

    def predict_diabetes_risk(self, state: PatientState) -> Optional[Dict[str, Any]]:
        """Predict probability of diabetes / poor control based on patient state."""
        if not self.load() or not self._diabetes_model:
            return None
            
        # Extract features matching the Diabetes model's expectations
        # Expected features: Pregnancies, Glucose, BloodPressure, BMI, DiabetesPedigreeFunction, Age
        
        # We need at least one lab value or vital to make a meaningful prediction
        if not state.latest_labs and not state.vitals:
            return None
            
        features = {}
        
        # Demographics
        features["Age"] = state.demographics.age if state.demographics.age is not None else float('nan')
        features["Pregnancies"] = 1.0 if any("pregnan" in c.display_name.lower() for c in state.active_conditions) else 0.0
        
        # Vitals
        features["BloodPressure"] = state.vitals.get("diastolic_bp").value if "diastolic_bp" in state.vitals else float('nan')
        features["BMI"] = state.vitals.get("bmi").value if "bmi" in state.vitals else float('nan')
        
        # Labs
        features["Glucose"] = state.latest_labs.get("glucose").value if "glucose" in state.latest_labs else float('nan')
        
        # We don't have family history in the basic state, impute with NaN
        features["DiabetesPedigreeFunction"] = float('nan')

        non_nan_count = sum(1 for v in features.values() if pd.notna(v))
        if non_nan_count < 2:
            return None
            
        df = pd.DataFrame([features])
        
        try:
            prob = self._diabetes_model.predict_proba(df)[0][1]
            return {
                "risk_score": float(prob),
                "model": self._diabetes_meta.get("model", "GradientBoosting"),
                "dataset": self._diabetes_meta.get("dataset", "Pima Indians Diabetes"),
                "features_used": non_nan_count,
                "top_features": list(self._diabetes_meta.get("feature_importances", {}).keys())[:3]
            }
        except Exception as e:
            logger.error(f"Diabetes prediction failed: {e}")
            return None

# Singleton instance
predictor = MLPredictor()
