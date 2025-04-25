"""
pip install -U dicta-onnx mishkal

wget https://github.com/thewh1teagle/dicta-onnx/releases/download/model-files-v1.0/dicta-1.0.int8.onnx
uv run examples/with_dicta_onnx.py
"""

from dicta_onnx import Dicta
from mishkal import phonemize

dicta = Dicta("./dicta-1.0.int8.onnx")
sentence = "בשנת 1948 השלים אפרים קישון את לימודיו בפיסול מתכת ובתולדות האמנות והחל לפרסם מאמרים הומוריסטיים על כל מה שרצה"
with_diacritics = dicta.add_diacritics(sentence)
phonemes = phonemize(with_diacritics)
print("Sentence: ", with_diacritics)
print("Phonemes: ", phonemes)
