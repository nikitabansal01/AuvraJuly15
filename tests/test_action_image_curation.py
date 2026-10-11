import hashlib
import pytest
from app.utils import action_images as images

@pytest.mark.parametrize('title,uri,corrected', [
    ('Swimming', 'https://images.example.test/reviewed-mismatch.png', True),
    ('  SWIMMING ', 'https://images.example.test/reviewed-mismatch.png', True),
    ('Resistance Training', 'https://images.example.test/reviewed-mismatch.png', False),
    ('Swimming', 'https://images.example.test/another-swimming.png', False),
    ('Swimming', None, False),
])
def test_only_the_reviewed_mismatched_subject_is_substituted(monkeypatch, title, uri, corrected):
    monkeypatch.setattr(images, '_SWIMMING_MISMATCH', hashlib.sha256(b'https://images.example.test/reviewed-mismatch.png').hexdigest())
    assert images.action_image_url(title, uri) == (images._SWIMMING_REVIEWED_IMAGE if corrected else uri)
