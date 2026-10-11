from types import SimpleNamespace
import hashlib
from unittest.mock import AsyncMock, Mock
import pytest
from sqlalchemy.dialects import postgresql
from app.services.image_library_service import ImageLibraryService


@pytest.mark.asyncio
async def test_swimming_cannot_reuse_a_seated_land_image_even_with_identical_embeddings():
    service = ImageLibraryService.__new__(ImageLibraryService)
    rows = [SimpleNamespace(id=1, image_url='https://res.cloudinary.com/fixture/land.png', prompt_text='Seated stretches', prompt_embedding=[1,0], used_by_users=[]),
            SimpleNamespace(id=2, image_url='https://res.cloudinary.com/fixture/water.png', prompt_text='Swimming in a pool', prompt_embedding=[1,0], used_by_users=[])]
    db=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda:rows)))
    result=await service._find_similar_image([1,0],'movement','hero','qa-one',db,requested_prompt='Swimming')
    assert result['id']==2


@pytest.mark.asyncio
async def test_last_resort_cache_rescue_obeys_the_same_water_boundary_and_hero_kind():
    service=ImageLibraryService.__new__(ImageLibraryService)
    rows=[SimpleNamespace(id=1,image_url='https://res.cloudinary.com/fixture/land.png',prompt_text='Chair yoga',prompt_embedding=[1,0],used_by_users=['qa-one'])]
    db=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda:rows)))
    result=await service._find_similar_image([1,0],'movement',None,'qa-one',db,requested_prompt='Swimming',threshold=0,include_seen=True)
    assert result is None
    sql=str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert "variant_type = 'hero'" in sql and 'variant_type IS NULL' in sql


def test_aquatic_generation_explicitly_describes_water_and_the_actual_activity():
    service=ImageLibraryService.__new__(ImageLibraryService)
    prompt=service._enhance_prompt('Swimming','movement','hero')
    assert 'swimming pool' in prompt and 'swimwear' in prompt and 'arm stroke' in prompt
    assert not service._compatible_image_subject('Walking','Swimming','movement')
    assert service._compatible_image_subject('Water walking','Aqua aerobics','movement')
    assert service._compatible_image_subject('Chickpea salad','Chickpeas','food')


@pytest.mark.asyncio
async def test_a_visually_rejected_asset_cannot_return_just_because_its_metadata_says_swimming(monkeypatch):
    service=ImageLibraryService.__new__(ImageLibraryService)
    uri='https://res.cloudinary.com/fixture/rejected.png'
    monkeypatch.setattr(service,'REJECTED_AQUATIC_ASSET_HASHES',frozenset({hashlib.sha256(uri.encode()).hexdigest()}))
    row=SimpleNamespace(id=1,image_url=uri,prompt_text='Swimming',prompt_embedding=[1,0],used_by_users=[])
    db=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda:[row])))
    assert await service._find_similar_image([1,0],'movement','hero','qa-one',db,requested_prompt='Swimming') is None
