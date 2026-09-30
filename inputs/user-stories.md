1. As a load tester, I want to generate a fresh, realistic dataset with a single command, so that I'm not hand-crafting CSVs every test run.
2. As a load tester, I want the same seed to produce byte-identical output, so that performance comparisons between runs are valid.
3. As a load tester, I want transactions to be statistically patternless, so that the data defeats simple anomaly detection and mirrors real-world variance.
4. As a load tester, I want the minimum target to be ₱4M with natural variance, so that the dataset stresses the reporting system at realistic volumes.
5. As a load tester, I want to spot-check samples and have the system learn from my corrections, so that future runs get more realistic over time.
6. As a load tester, I want the process to complete without further human intervention after kickoff, so that I can start a run and walk away.
7. As a load tester, I want a final CSV with date, quantity, unit price, and item description, so that it plugs directly into my reporting system's import pipeline.
8. As a load tester, I want to run the method repeatedly without repeating myself, so that each quarter's load test takes the same 30 seconds to configure.