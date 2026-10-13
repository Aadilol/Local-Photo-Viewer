"""Brand renaming safeguard: no literal obsolete identifiers in new project."""
import unittest
from pathlib import Path

class TestNaming(unittest.TestCase):
    def test_only_current_brand_identifiers(self):
        root = Path(__file__).resolve().parents[1]
        old = (''.join(('Photo','Date','Restorer')),
               ' '.join(('Photo','Date','Restorer')),
               ''.join(('photo','date','restorer')))
        for path in root.rglob('*'):
            if not path.is_file() or set(path.parts) & {'__pycache__', '.venv', 'build', 'dist'}:
                continue
            if path.suffix.lower() in ('.ico', '.png', '.jpg', '.jpeg', '.webp', '.zip', '.pyc'):
                continue
            content = path.read_text(encoding='utf8')
            for word in old:
                # This test file contains only split forms of old identifiers.
                self.assertNotIn(word, content, str(path))

if __name__ == '__main__':
    unittest.main()
