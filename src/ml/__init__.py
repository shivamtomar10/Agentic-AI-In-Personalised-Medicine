"""ML module: real machine-learning risk models trained on public medical datasets.

Datasets used:
  1. UCI Chronic Kidney Disease Dataset (Soundarya Somasundaram, UCI ML Repository, 2015)
     Source: https://archive.ics.uci.edu/dataset/336/chronic+kidney+disease
     400 patients, 24 clinical features, binary CKD classification
     
  2. Pima Indians Diabetes Dataset (National Institute of Diabetes and Digestive and Kidney Diseases)
     Source: https://www.kaggle.com/datasets/uciml/pima-indians-diabetes-database
     768 female patients aged >=21, 8 features, binary diabetes classification

Models trained:
  - Random Forest Classifier (CKD risk prediction)
  - Gradient Boosting Classifier (Diabetes complication / poor glycaemic control risk)

These models are trained once via:  python run.py train-models
and saved to data/ml_models/ for inference by the MLRiskAgent.

The ML predictions are ADDITIVE — they provide probabilistic risk scores
on top of (not instead of) the rule-based safety checks. The rule-based
guardrails (ADA 2024, KDIGO 2022, FDA labels) always run regardless.
"""
