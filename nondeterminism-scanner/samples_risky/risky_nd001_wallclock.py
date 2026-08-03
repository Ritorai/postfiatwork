"""Trips ND001_WALL_CLOCK via every documented target, plus aliased imports."""
import datetime
import time
from datetime import datetime as dt2


def now_variants():
    a = datetime.datetime.now()
    b = datetime.datetime.utcnow()
    c = datetime.date.today()
    d = time.time()
    e = time.monotonic()
    f = dt2.now()
    return a, b, c, d, e, f
