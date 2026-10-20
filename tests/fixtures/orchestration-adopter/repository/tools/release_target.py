"""Recording fake for one release target."""

import sys

sys.path.insert(0, "tools")
from record import record

record("release", *sys.argv[1:])
