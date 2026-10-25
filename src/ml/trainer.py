"""ML model trainer: trains and persists scikit-learn models on real public medical datasets.

Run once via:  python run.py train-models

Models trained:
  1. CKD Risk Model  — Random Forest Classifier on UCI CKD Dataset (400 patients)
     Task: predict whether a patient has chronic kidney disease
     Input features: age, blood_pressure, serum_creatinine, blood_glucose_random,
                     haemoglobin, albumin, blood_urea, packed_cell_volume,
                     hypertension (bool), diabetes_mellitus (bool)

  2. Diabetes Risk Model — Gradient Boosting Classifier on Pima Indians Dataset (768 patients)
     Task: predict diabetes onset / poor glycaemic control risk
     Input features: age, glucose, blood_pressure, bmi, diabetes_pedigree_function, pregnancies

Both models:
  - Use SimpleImputer(strategy='median') for missing values (common in medical data)
  - Use StandardScaler for numeric normalisation
  - Are saved as joblib files for fast loading at inference time
  - Report cross-validated accuracy, ROC-AUC and feature importances
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (classification_report, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.config import get_settings
from src.ml.datasets import (load_ckd_dataset, load_pima_dataset,
                              preprocess_ckd, preprocess_pima)

_MODELS_DIR = get_settings().knowledge_dir.parent / "ml_models"
_MODELS_DIR.mkdir(parents=True, exist_ok=True)

CKD_MODEL_PATH = _MODELS_DIR / "ckd_risk_rf.joblib"
DIABETES_MODEL_PATH = _MODELS_DIR / "diabetes_risk_gb.joblib"
CKD_META_PATH = _MODELS_DIR / "ckd_risk_meta.json"
DIABETES_META_PATH = _MODELS_DIR / "diabetes_risk_meta.json"


def _make_ckd_pipeline() -> Pipeline:
    """Random Forest pipeline for CKD classification.
    
    Random Forest is preferred over deep learning for this dataset because:
    - Dataset size: only 400 patients (neural nets overfit here)
    - Interpretability: feature importances show what drives predictions
    - Robustness to missing values (with imputation)
    - Proven performance on tabular medical data (Fernandez-Delgado et al., 2014)
    """
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=4,
            class_weight="balanced",   # handles class imbalance
            random_state=42,
            n_jobs=-1,
        )),
    ])


def _make_diabetes_pipeline() -> Pipeline:
    """Gradient Boosting pipeline for diabetes risk classification.
    
    Gradient Boosting is preferred here because:
    - Handles the non-linear relationships in the Pima dataset well
    - Robust to the zero-value imputation issue in this dataset
    - Better calibrated probabilities than basic decision trees
    - Consistently strong on medical classification benchmarks
    """
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(
            n_estimators=150,
            max_depth=4,
            learning_rate=0.08,
            subsample=0.8,
            random_state=42,
        )),
    ])


def train_ckd_model(verbose: bool = True) -> Dict[str, Any]:
    """Train and save the CKD risk Random Forest model.
    
    Returns metadata dict with dataset info, performance metrics, and feature importances.
    """
    if verbose:
        print("Loading UCI CKD dataset...")
    df = load_ckd_dataset()
    X, y = preprocess_ckd(df)

    if verbose:
        print(f"  Dataset: {len(X)} patients, {X.shape[1]} features, "
              f"{y.sum()} CKD / {(y == 0).sum()} not-CKD")
        print(f"  Features: {list(X.columns)}")

    pipe = _make_ckd_pipeline()

    # Cross-validated performance (5-fold stratified)
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    # Fill NaN for CV scoring
    X_filled = X.fillna(X.median(numeric_only=True))
    cv_acc = cross_val_score(pipe, X_filled, y, cv=cv, scoring="accuracy")
    cv_auc = cross_val_score(pipe, X_filled, y, cv=cv, scoring="roc_auc")

    # Final fit on all data
    pipe.fit(X_filled, y)

    # Feature importances from the Random Forest
    feat_names = list(X.columns)
    importances = pipe.named_steps["clf"].feature_importances_
    feat_imp = dict(sorted(zip(feat_names, importances.tolist()), key=lambda x: -x[1]))

    meta = {
        "model": "RandomForestClassifier",
        "dataset": "UCI Chronic Kidney Disease Dataset",
        "dataset_citation": "Soundarya Somasundaram (2015). UCI ML Repository. https://doi.org/10.24432/C5G020",
        "dataset_license": "CC BY 4.0",
        "n_patients": int(len(X)),
        "n_features": int(X.shape[1]),
        "features": feat_names,
        "n_ckd": int(y.sum()),
        "n_not_ckd": int((y == 0).sum()),
        "cv_accuracy_mean": round(float(cv_acc.mean()), 4),
        "cv_accuracy_std": round(float(cv_acc.std()), 4),
        "cv_roc_auc_mean": round(float(cv_auc.mean()), 4),
        "cv_roc_auc_std": round(float(cv_auc.std()), 4),
        "feature_importances": feat_imp,
        "hyperparameters": {
            "n_estimators": 200, "max_depth": 8,
            "min_samples_leaf": 4, "class_weight": "balanced",
        },
        "limitation": (
            "Trained on 400 patients from one Indian hospital. "
            "Performance may differ in other populations or demographics. "
            "This score is SUPPLEMENTARY to rule-based checks — not a replacement."
        ),
    }

    joblib.dump(pipe, CKD_MODEL_PATH)
    CKD_META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if verbose:
        print(f"  CV Accuracy: {meta['cv_accuracy_mean']:.3f} ± {meta['cv_accuracy_std']:.3f}")
        print(f"  CV ROC-AUC:  {meta['cv_roc_auc_mean']:.3f} ± {meta['cv_roc_auc_std']:.3f}")
        print(f"  Top features: {list(feat_imp.keys())[:3]}")
        print(f"  Saved -> {CKD_MODEL_PATH}")

    return meta


def train_diabetes_model(verbose: bool = True) -> Dict[str, Any]:
    """Train and save the Diabetes risk Gradient Boosting model."""
    if verbose:
        print("Loading Pima Indians Diabetes dataset...")
    df = load_pima_dataset()
    X, y = preprocess_pima(df)

    if verbose:
        print(f"  Dataset: {len(X)} patients, {X.shape[1]} features, "
              f"{y.sum()} diabetic / {(y == 0).sum()} non-diabetic")
        print(f"  Features: {list(X.columns)}")

    pipe = _make_diabetes_pipeline()

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    X_filled = X.fillna(X.median(numeric_only=True))
    cv_acc = cross_val_score(pipe, X_filled, y, cv=cv, scoring="accuracy")
    cv_auc = cross_val_score(pipe, X_filled, y, cv=cv, scoring="roc_auc")

    pipe.fit(X_filled, y)

    feat_names = list(X.columns)
    importances = pipe.named_steps["clf"].feature_importances_
    feat_imp = dict(sorted(zip(feat_names, importances.tolist()), key=lambda x: -x[1]))

    meta = {
        "model": "GradientBoostingClassifier",
        "dataset": "Pima Indians Diabetes Dataset",
        "dataset_citation": (
            "Smith, J.W. et al. (1988). Using the ADAP learning algorithm to forecast the onset "
            "of diabetes mellitus. Symposium on Computer Applications in Medical Care. "
            "National Institute of Diabetes and Digestive and Kidney Diseases (NIDDK)."
        ),
        "dataset_license": "Public domain (US Government data)",
        "n_patients": int(len(X)),
        "n_features": int(X.shape[1]),
        "features": feat_names,
        "n_diabetic": int(y.sum()),
        "n_non_diabetic": int((y == 0).sum()),
        "cv_accuracy_mean": round(float(cv_acc.mean()), 4),
        "cv_accuracy_std": round(float(cv_acc.std()), 4),
        "cv_roc_auc_mean": round(float(cv_auc.mean()), 4),
        "cv_roc_auc_std": round(float(cv_auc.std()), 4),
        "feature_importances": feat_imp,
        "hyperparameters": {
            "n_estimators": 150, "max_depth": 4,
            "learning_rate": 0.08, "subsample": 0.8,
        },
        "limitation": (
            "Trained on Pima Indian heritage females aged >=21. "
            "May not generalise to other demographics or ethnicities. "
            "This score is SUPPLEMENTARY to rule-based checks — not a replacement."
        ),
    }

    joblib.dump(pipe, DIABETES_MODEL_PATH)
    DIABETES_META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if verbose:
        print(f"  CV Accuracy: {meta['cv_accuracy_mean']:.3f} ± {meta['cv_accuracy_std']:.3f}")
        print(f"  CV ROC-AUC:  {meta['cv_roc_auc_mean']:.3f} ± {meta['cv_roc_auc_std']:.3f}")
        print(f"  Top features: {list(feat_imp.keys())[:3]}")
        print(f"  Saved -> {DIABETES_MODEL_PATH}")

    return meta


def train_all(verbose: bool = True) -> Dict[str, Any]:
    """Train both models and return combined metadata."""
    print("=" * 60)
    print("CareTrace ML Model Training")
    print("=" * 60)
    print()
    print("[1/2] CKD Risk Model (Random Forest)")
    print("-" * 40)
    ckd_meta = train_ckd_model(verbose=verbose)
    print()
    print("[2/2] Diabetes Risk Model (Gradient Boosting)")
    print("-" * 40)
    dia_meta = train_diabetes_model(verbose=verbose)
    print()
    print("=" * 60)
    print("Training complete. Both models are ready for inference.")
    print(f"Models saved to: {_MODELS_DIR}")
    print("=" * 60)
    return {"ckd": ckd_meta, "diabetes": dia_meta}


def models_exist() -> bool:
    """Return True if trained model files exist on disk."""
    return CKD_MODEL_PATH.exists() and DIABETES_MODEL_PATH.exists()
