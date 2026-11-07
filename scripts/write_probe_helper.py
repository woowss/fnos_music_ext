import os
import sys

path = sys.argv[1]
try:
    with open(path, "wb") as stream:
        stream.write(b"x")
    print("WRITE_ACCEPTED", path)
except OSError as exc:
    print("WRITE_REJECTED", exc.errno, exc.strerror, path)
