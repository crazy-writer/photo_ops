import os
from typing import Dict, Optional, Union

PathLike = Union[str, os.PathLike]

# Workers specification — any of:
#   False / None / 0  → serial (single thread)
#   True              → 10 threads
#   int > 0           → exact thread count
#   int < 0           → cpu_count − abs(N)  (leave N CPUs free)
#   float 0-1         → fraction of cpu_count  (e.g. 0.5 = 50%)
#   "auto"            → adaptive (ramps up/down based on CPU load)
#   "max" / "all"     → all logical CPUs
#   "N" / "Nw" / "Nt" → N threads  (e.g. "4", "4w", "4t")
#   "N%"              → N% of logical CPUs  (e.g. "50%")
WorkerSpec = Union[bool, int, float, str, None]

BulkResult = Dict[str, Union[str, dict]]
