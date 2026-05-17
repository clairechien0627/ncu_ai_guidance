import os
import sys

# Ensure backend/ is on sys.path for all tests regardless of subdirectory depth.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
