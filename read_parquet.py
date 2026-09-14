import pandas as pd
df = pd.read_parquet('data/pumpfun_training_dataset.parquet')
print("Shape:", df.shape)
print("Columns:", list(df.columns))
print("First row:", df.iloc[0].to_dict())
