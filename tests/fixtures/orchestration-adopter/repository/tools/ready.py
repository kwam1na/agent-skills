"""Recording fake for the forge's readiness step: always ready."""

import sys

sys.path.insert(0, "tools")
from record import record

record("ready", *sys.argv[1:])
