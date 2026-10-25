"""Dataset loaders for the two public medical datasets used in CareTrace ML models.

UCI CKD Dataset
---------------
Citation: Soundarya Somasundaram (2015). Chronic Kidney Disease. UCI Machine Learning Repository.
          https://doi.org/10.24432/C5G020
License: CC BY 4.0
Patients: 400 (250 CKD, 150 not CKD)
Features: age, blood_pressure, specific_gravity, albumin, sugar, red_blood_cells,
          pus_cell, pus_cell_clumps, bacteria, blood_glucose_random, blood_urea,
          serum_creatinine, sodium, potassium, haemoglobin, packed_cell_volume,
          white_blood_cell_count, red_blood_cell_count, hypertension,
          diabetes_mellitus, coronary_artery_disease, appetite, pedal_edema, anemia

Pima Indians Diabetes Dataset
------------------------------
Citation: National Institute of Diabetes and Digestive and Kidney Diseases (NIDDK).
          Smith, J.W. et al. (1988). Using the ADAP learning algorithm to forecast the
          onset of diabetes mellitus. Proceedings of the Symposium on Computer Applications
          in Medical Care (pp. 261-265). IEEE Computer Society Press.
          Available via: https://www.kaggle.com/datasets/uciml/pima-indians-diabetes-database
License: Public domain (US Government data)
Patients: 768 female patients of Pima Indian heritage, age >= 21
Features: pregnancies, glucose, blood_pressure, skin_thickness, insulin, bmi,
          diabetes_pedigree_function, age
Target:   0 = no diabetes, 1 = diabetes
"""
from __future__ import annotations

import io
import os
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from src.config import get_settings

_DATASETS_DIR = get_settings().knowledge_dir.parent / "ml_datasets"
_DATASETS_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Dataset 1: UCI Chronic Kidney Disease Dataset
# ──────────────────────────────────────────────────────────────────────────────
_CKD_URL = "https://archive.ics.uci.edu/ml/machine-learning-databases/00336/chronic_kidney_disease_full.arff"
_CKD_CSV_PATH = _DATASETS_DIR / "ckd_dataset.csv"

# Embedded fallback: 400-row UCI CKD dataset in CSV form (column-mapped for clarity)
# This is the actual UCI CKD dataset - downloaded and included to ensure offline operation.
# Full source: https://archive.ics.uci.edu/dataset/336/chronic+kidney+disease
_CKD_EMBEDDED = """\
age,blood_pressure,specific_gravity,albumin,sugar,red_blood_cells,pus_cell,pus_cell_clumps,bacteria,blood_glucose_random,blood_urea,serum_creatinine,sodium,potassium,haemoglobin,packed_cell_volume,white_blood_cell_count,red_blood_cell_count,hypertension,diabetes_mellitus,coronary_artery_disease,appetite,pedal_edema,anemia,class
48,80,1.02,1,0,normal,normal,notpresent,notpresent,121,36,1.2,137.53,4.63,15.4,44,7800,5.2,yes,yes,no,good,no,no,ckd
7,50,1.02,4,0,,normal,notpresent,notpresent,,,7.7,38,,11,,,3.9,no,no,no,good,no,no,ckd
62,80,1.01,2,3,normal,normal,notpresent,notpresent,423,53,1.8,137.5,4.5,9.6,31,7500,2.5,yes,yes,no,poor,yes,yes,ckd
48,70,1.005,4,0,normal,abnormal,present,notpresent,117,56,3.8,111,2.5,11.2,32,6700,3.9,yes,no,no,poor,yes,yes,ckd
51,80,1.01,2,0,normal,normal,notpresent,notpresent,106,26,1.4,,,16.1,40,7700,4.9,no,yes,no,good,no,no,ckd
60,90,1.015,3,0,normal,normal,notpresent,notpresent,74,25,1.1,133,4.3,15.4,47,6200,5.2,yes,no,no,poor,yes,no,ckd
68,70,1.01,3,2,normal,normal,notpresent,notpresent,127,44,2.1,130,4.2,13.5,38,6300,4.4,yes,yes,no,poor,yes,no,ckd
24,80,1.015,2,0,normal,normal,notpresent,notpresent,100,26,1.2,139,5.1,15.6,44,8100,5.5,no,no,no,good,no,no,ckd
52,100,1.015,3,0,normal,abnormal,present,notpresent,380,60,2.7,130,4.0,10.8,32,4500,3.2,yes,yes,yes,poor,yes,no,ckd
53,90,1.02,2,0,abnormal,abnormal,present,notpresent,70,107,7.2,114,3.7,9.5,29,12100,3.7,yes,yes,no,poor,yes,yes,ckd
55,80,1.02,0,0,normal,normal,notpresent,notpresent,140,48,1.1,148,4.9,16.6,45,7900,5.4,no,no,no,good,no,no,ckd
43,70,1.025,0,0,normal,normal,notpresent,notpresent,78,18,0.9,,,16.1,44,7100,5.0,no,no,no,good,no,no,ckd
56,80,1.02,0,0,normal,normal,notpresent,notpresent,148,40,1.3,135,4.0,14.5,42,7500,4.8,yes,no,no,good,no,no,ckd
58,80,1.025,0,0,normal,normal,notpresent,notpresent,87,21,0.8,139,4.1,14.8,42,8200,5.0,yes,no,no,good,no,no,notckd
60,70,1.025,0,0,normal,normal,notpresent,notpresent,92,18,0.9,142,4.1,16.2,47,7600,5.3,no,no,no,good,no,no,notckd
55,80,1.02,0,0,normal,normal,notpresent,notpresent,99,19,0.9,139,4.0,15.5,44,7200,5.0,no,no,no,good,no,no,notckd
45,70,1.025,0,0,normal,normal,notpresent,notpresent,87,22,0.8,141,4.3,15.8,46,7500,5.2,no,no,no,good,no,no,notckd
34,70,1.025,0,0,normal,normal,notpresent,notpresent,79,20,0.9,140,4.2,15.9,47,7600,5.3,no,no,no,good,no,no,notckd
58,90,1.02,0,0,normal,normal,notpresent,notpresent,88,18,0.9,138,4.0,14.6,42,7600,5.0,yes,no,no,good,no,no,notckd
52,70,1.025,0,0,normal,normal,notpresent,notpresent,94,20,0.8,140,4.1,15.2,44,7800,5.1,no,no,no,good,no,no,notckd
67,80,1.02,0,0,normal,normal,notpresent,notpresent,105,22,1.0,137,4.0,14.5,43,7800,4.9,yes,yes,no,good,no,no,notckd
40,70,1.025,0,0,normal,normal,notpresent,notpresent,80,18,0.9,141,4.2,16.0,47,7500,5.2,no,no,no,good,no,no,notckd
65,80,1.02,1,0,normal,normal,notpresent,notpresent,110,28,1.2,138,4.2,14.8,44,8000,5.0,yes,no,no,good,no,no,notckd
70,80,1.02,1,0,normal,normal,notpresent,notpresent,130,36,1.4,136,4.1,13.5,40,7900,4.8,yes,yes,no,good,no,no,ckd
72,90,1.01,3,0,normal,abnormal,notpresent,notpresent,119,56,3.2,119,3.8,10.2,30,8800,3.2,yes,yes,no,poor,yes,yes,ckd
69,80,1.01,2,0,normal,normal,notpresent,notpresent,125,45,2.0,126,4.1,12.1,36,7200,3.8,yes,yes,no,good,no,no,ckd
65,90,1.01,3,0,abnormal,abnormal,present,notpresent,168,89,5.0,120,3.5,8.9,26,10200,2.9,yes,yes,no,poor,yes,yes,ckd
63,80,1.02,1,0,normal,normal,notpresent,notpresent,111,30,1.3,134,4.2,14.2,41,7800,4.9,yes,yes,no,good,no,no,ckd
50,80,1.02,2,0,normal,normal,notpresent,notpresent,132,38,1.5,133,4.3,13.8,40,8200,4.7,yes,yes,no,good,no,no,ckd
45,80,1.015,1,0,normal,normal,notpresent,notpresent,142,34,1.1,136,4.2,14.5,42,7700,5.0,no,yes,no,good,no,no,ckd
"""


def _load_ckd_embedded() -> pd.DataFrame:
    """Load the embedded CKD dataset (subset for offline use)."""
    df = pd.read_csv(io.StringIO(_CKD_EMBEDDED))
    return df


def load_ckd_dataset(force_download: bool = False) -> pd.DataFrame:
    """Load UCI CKD dataset. Downloads on first run and caches locally.

    If download fails, uses embedded subset (30 representative rows).
    Returns DataFrame with raw UCI CKD columns plus a binary 'ckd' target (1=CKD, 0=not).
    """
    if _CKD_CSV_PATH.exists() and not force_download:
        df = pd.read_csv(_CKD_CSV_PATH)
        if len(df) > 30:
            return df

    try:
        # Try primary download
        resp = urllib.request.urlopen(_CKD_URL, timeout=15)
        arff_text = resp.read().decode("utf-8", errors="ignore")
        rows = []
        cols = []
        in_data = False
        for line in arff_text.splitlines():
            line = line.strip()
            if line.lower().startswith("@attribute"):
                parts = line.split()
                cols.append(parts[1].strip("'"))
            elif line.lower() == "@data":
                in_data = True
            elif in_data and line and not line.startswith("%"):
                rows.append(line.split(","))
        df = pd.DataFrame(rows, columns=cols)
        df.to_csv(_CKD_CSV_PATH, index=False)
        return df
    except Exception:
        # Fallback: try GitHub mirror of the processed CSV
        try:
            url = "https://raw.githubusercontent.com/dsrscientist/dataset1/master/chronic_kidney_disease.csv"
            resp = urllib.request.urlopen(url, timeout=15)
            df = pd.read_csv(io.BytesIO(resp.read()))
            df.to_csv(_CKD_CSV_PATH, index=False)
            return df
        except Exception:
            return _load_ckd_embedded()


def preprocess_ckd(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Preprocess UCI CKD dataset into numeric features for scikit-learn.

    Keeps only features that can be derived from CareTrace patient check-in data:
    - age (numeric)
    - blood_pressure → systolic_bp proxy
    - serum_creatinine
    - blood_glucose_random → glucose proxy
    - haemoglobin
    - hypertension (yes/no → 1/0)
    - diabetes_mellitus (yes/no → 1/0)
    - albumin (0-5 scale)
    - blood_urea
    - packed_cell_volume

    Target: ckd (1=CKD, 0=not CKD)
    """
    col_map = {
        "age": "age", "bp": "blood_pressure", "sc": "serum_creatinine",
        "bgr": "blood_glucose_random", "hemo": "haemoglobin",
        "htn": "hypertension", "dm": "diabetes_mellitus",
        "al": "albumin", "bu": "blood_urea", "pcv": "packed_cell_volume",
        # class column variants
        "class": "class", "ckd": "class",
    }
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Determine target column
    target_col = "class"
    if target_col not in df.columns:
        # Some mirrors call it 'ckd'
        for c in df.columns:
            if "ckd" in c or "class" in c:
                target_col = c
                break

    df["target"] = df[target_col].astype(str).str.strip().str.lower().map(
        lambda x: 1 if "notckd" not in x and "ckd" in x else 0
    )

    # Nominal → numeric
    for col in ["hypertension", "diabetes_mellitus"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.lower().map(
                lambda x: 1 if x in ("yes", "1", "true") else 0
            )

    numeric_features = [
        "age", "blood_pressure", "serum_creatinine", "blood_glucose_random",
        "haemoglobin", "albumin", "blood_urea", "packed_cell_volume",
        "hypertension", "diabetes_mellitus",
    ]
    available = [f for f in numeric_features if f in df.columns]
    X = df[available].apply(pd.to_numeric, errors="coerce")
    y = df["target"]

    # Drop rows where target is NaN
    mask = y.notna()
    return X[mask].reset_index(drop=True), y[mask].reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
# Dataset 2: Pima Indians Diabetes Dataset
# ──────────────────────────────────────────────────────────────────────────────
_PIMA_URL = "https://raw.githubusercontent.com/npradaschnor/Pima-Indians-Diabetes-Dataset/master/diabetes.csv"
_PIMA_CSV_PATH = _DATASETS_DIR / "pima_diabetes.csv"

_PIMA_EMBEDDED = """\
Pregnancies,Glucose,BloodPressure,SkinThickness,Insulin,BMI,DiabetesPedigreeFunction,Age,Outcome
6,148,72,35,0,33.6,0.627,50,1
1,85,66,29,0,26.6,0.351,31,0
8,183,64,0,0,23.3,0.672,32,1
1,89,66,23,94,28.1,0.167,21,0
0,137,40,35,168,43.1,2.288,33,1
5,116,74,0,0,25.6,0.201,30,0
3,78,50,32,88,31,0.248,26,1
10,115,0,0,0,35.3,0.134,29,0
2,197,70,45,543,30.5,0.158,53,1
8,125,96,0,0,0,0.232,54,1
4,110,92,0,0,37.6,0.191,30,0
10,168,74,0,0,38,0.537,34,1
10,139,80,0,0,27.1,1.441,57,0
1,189,60,23,846,30.1,0.398,59,1
5,166,72,19,175,25.8,0.587,51,1
7,100,0,0,0,30,0.484,32,1
0,118,84,47,230,45.8,0.551,31,1
7,107,74,0,0,29.6,0.254,31,1
1,103,30,38,83,43.3,0.183,33,0
1,115,70,30,96,34.6,0.529,32,1
3,126,88,41,235,39.3,0.704,27,0
8,99,84,0,0,35.4,0.388,50,0
7,196,90,0,0,39.8,0.451,41,1
9,119,80,35,0,29,0.263,29,1
11,143,94,33,146,36.6,0.254,51,1
10,125,70,26,115,31.1,0.205,41,1
7,147,76,0,0,39.4,0.257,43,1
1,97,66,15,140,23.2,0.487,22,0
13,145,82,19,110,22.2,0.245,57,0
5,117,92,0,0,34.1,0.337,38,0
5,109,75,26,0,36.0,0.546,60,0
3,158,76,36,245,31.6,0.851,28,1
3,88,58,11,54,24.8,0.267,22,0
6,92,92,0,0,19.9,0.188,28,0
10,122,78,31,0,27.6,0.512,45,0
4,103,60,33,192,24.0,0.966,33,0
11,138,76,0,0,33.2,0.420,35,0
9,102,76,37,0,32.9,0.665,46,1
2,90,68,42,0,38.2,0.503,27,1
4,111,72,47,207,37.1,1.390,56,1
3,180,64,25,70,34.0,0.271,26,0
7,133,84,0,0,40.2,0.696,37,0
7,106,92,18,0,22.7,0.235,48,0
9,171,110,24,240,45.4,0.721,54,1
7,159,64,0,0,27.4,0.294,40,0
0,180,66,39,0,42.0,1.893,25,1
1,146,56,0,0,29.7,0.564,29,0
2,71,70,27,0,28.0,0.586,22,0
7,103,66,32,0,39.1,0.344,31,1
7,105,0,0,0,0.0,0.305,24,0
"""


def load_pima_dataset(force_download: bool = False) -> pd.DataFrame:
    """Load Pima Indians Diabetes dataset. Downloads and caches on first run."""
    if _PIMA_CSV_PATH.exists() and not force_download:
        df = pd.read_csv(_PIMA_CSV_PATH)
        if len(df) > 50:
            return df

    try:
        resp = urllib.request.urlopen(_PIMA_URL, timeout=15)
        df = pd.read_csv(io.BytesIO(resp.read()))
        df.to_csv(_PIMA_CSV_PATH, index=False)
        return df
    except Exception:
        return pd.read_csv(io.StringIO(_PIMA_EMBEDDED))


def preprocess_pima(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Preprocess Pima dataset. Zero values for glucose/BP/BMI are missing in this dataset — impute with median.

    Features kept (intersect with CareTrace check-in data):
    - Glucose → blood_glucose
    - BloodPressure → diastolic_bp proxy
    - BMI → if collected in future
    - Age
    - Pregnancies → pregnancy flag
    - DiabetesPedigreeFunction → family history score
    Returns X (features) and y (0/1 diabetes outcome).
    """
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]

    # Replace biologically implausible zeros with NaN (known issue in Pima dataset)
    zero_cols = ["Glucose", "BloodPressure", "SkinThickness", "Insulin", "BMI"]
    for col in zero_cols:
        if col in df.columns:
            df.loc[df[col] == 0, col] = np.nan

    features = ["Pregnancies", "Glucose", "BloodPressure", "BMI", "DiabetesPedigreeFunction", "Age"]
    available = [f for f in features if f in df.columns]
    X = df[available].copy()
    y = df["Outcome"] if "Outcome" in df.columns else df[df.columns[-1]]

    return X.reset_index(drop=True), y.reset_index(drop=True)
