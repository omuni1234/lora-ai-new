"""Bounded-memory analysis: python scripts/analyze.py data/runs/JOB_ID"""
import argparse
from collections import Counter
from pathlib import Path
import pyarrow.dataset as ds

p=argparse.ArgumentParser();p.add_argument('run');a=p.parse_args()
root=Path(a.run)
counts=Counter()
# Select only GW uplinks; DL and non-target ED records are a different population.
scanner=ds.dataset(root/'receptions',format='parquet').scanner(
    columns=['outcome'],filter=(ds.field('direction')=='UL') & (ds.field('receiver_kind')=='GW'),batch_size=8192)
for batch in scanner.to_batches():
    counts.update(batch.column('outcome').to_pylist())
print(dict(counts))
try:
    import matplotlib.pyplot as plt
    plt.bar(counts.keys(),counts.values());plt.ylabel('GW reception attempts');plt.xticks(rotation=25,ha='right')
    plt.tight_layout();plt.savefig(root/'loss-breakdown.png',dpi=180)
except ImportError:
    print('Install .[analysis] to generate the plot.')
