"""Export the actual Python-generated browser script for JavaScript validation."""
import re
import sys
from manual_preview import HTML

sys.stdout.reconfigure(encoding='utf-8')
print(re.search(r'<script>(.*?)</script>',HTML,re.S)[1])
