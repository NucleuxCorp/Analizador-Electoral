"""Single source of truth for the project version.

Convention (v0.25.0b+):
  MAJOR.MINOR.PATCH[letter]
  
  - Letter increments per deploy within the same batch:
    0.25.0a -> 0.25.0b -> 0.25.0c
  - When a batch is completed (significant changes), bump PATCH and drop letter:
    0.25.0c -> 0.25.1, then 0.25.1a -> 0.25.1b -> ...
  - Bump BEFORE pushing to develop.

Example: 0.25.0b = second deploy of batch 0.25.0.
"""

__version__ = "0.25.0h"
