#!/bin/bash -l

### before submitting change results --> results_job in make.py line 98 + 128 for different output folder

#SBATCH --job-name=gammapy-benchmarks
### Time your job needs to execute, e. g. hh:mm:ss
#SBATCH --time=01:15:00
## #SBATCH --output=./out/     # not recommended for RRZE clusters

### for clean working environment
#SBATCH --export=NONE 
unset SLURM_EXPORT_ENV  # after that no more SBATCH commands can be defined

### The last part consists of regular shell commands:
conda activate gammapy-1.0
### Change to working directory
cd /home/hpc/caph/mppi086h/woody/software/gammapy-benchmarks/benchmarks

### Run your parallel application
python make.py run-benchmark all

### type in shell: sbatch submit_jobs.sh