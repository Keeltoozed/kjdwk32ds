import joblib
try:
    model = joblib.load("pro_model.pkl")
    print(type(model))
    import xgboost as xgb
    if isinstance(model, xgb.XGBClassifier) or isinstance(model, xgb.XGBModel):
        model.save_model("pro_model.json")
        print("pro_model converted to json!")
except Exception as e:
    print(f"Error: {e}")
