#!/usr/bin/env python3
"""
Test script for the real research citation system.
Run this to verify PubMed/OpenAlex/Semantic Scholar APIs work correctly.
"""
import asyncio
import sys
sys.path.insert(0, '/Users/mohanganesh/AUVRA/AuvraJuly15')

async def test_pubmed_service():
    """Test the PubMed service with multiple queries."""
    import httpx
    
    print("=" * 60)
    print("🧪 TESTING REAL RESEARCH CITATION SYSTEM")
    print("=" * 60)
    
    # Test queries that should return results
    test_cases = [
        {
            "query": "cinnamon insulin women",
            "action_title": "Cinnamon Oat Bowl",
            "category": "food",
            "hormone": "insulin"
        },
        {
            "query": "yoga cortisol stress women",
            "action_title": "Morning Yoga Flow",
            "category": "movement", 
            "hormone": "cortisol"
        },
        {
            "query": "meditation anxiety women",
            "action_title": "Evening Meditation",
            "category": "mindfulness",
            "hormone": "cortisol"
        }
    ]
    
    # Test PubMed API directly first
    print("\n📬 Testing PubMed API directly...")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Test 1: PubMed Search
        try:
            params = {
                "db": "pubmed",
                "term": "(cinnamon) AND (insulin) AND (women OR female)",
                "retmax": 3,
                "retmode": "json",
                "sort": "relevance",
                "datetype": "pdat",
                "mindate": "2010",
                "maxdate": "2025"
            }
            
            response = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params=params
            )
            response.raise_for_status()
            data = response.json()
            pmids = data.get("esearchresult", {}).get("idlist", [])
            
            if pmids:
                print(f"  ✅ PubMed Search works! Found {len(pmids)} papers")
                print(f"     PMIDs: {pmids}")
                
                # Test 2: Fetch paper details
                await asyncio.sleep(0.4)  # Rate limit
                
                fetch_response = await client.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    params={
                        "db": "pubmed",
                        "id": pmids[0],
                        "retmode": "xml"
                    }
                )
                fetch_response.raise_for_status()
                
                import xml.etree.ElementTree as ET
                root = ET.fromstring(fetch_response.content)
                article = root.find(".//PubmedArticle")
                
                if article is not None:
                    title = article.find(".//ArticleTitle")
                    journal = article.find(".//Journal/Title")
                    abstract_parts = article.findall(".//AbstractText")
                    
                    print(f"  ✅ PubMed Fetch works!")
                    print(f"     PMID: {pmids[0]}")
                    print(f"     Title: {title.text[:80] if title is not None and title.text else 'N/A'}...")
                    print(f"     Journal: {journal.text if journal is not None else 'N/A'}")
                    print(f"     Has Abstract: {len(abstract_parts) > 0}")
                    print(f"     Link: https://pubmed.ncbi.nlm.nih.gov/{pmids[0]}/")
                else:
                    print("  ⚠️ PubMed Fetch returned no article")
            else:
                print("  ⚠️ PubMed Search returned no results")
                
        except Exception as e:
            print(f"  ❌ PubMed API Error: {e}")
    
    # Test OpenAlex API
    print("\n📬 Testing OpenAlex API...")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(
                "https://api.openalex.org/works",
                params={
                    "search": "cinnamon insulin women",
                    "filter": "type:article,from_publication_date:2010-01-01",
                    "per-page": 1,
                    "mailto": "auvra@app.com"
                }
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            
            if results:
                paper = results[0]
                print(f"  ✅ OpenAlex works!")
                print(f"     Title: {paper.get('title', 'N/A')[:80]}...")
                print(f"     Year: {paper.get('publication_year')}")
                print(f"     DOI: {paper.get('doi', 'N/A')}")
            else:
                print("  ⚠️ OpenAlex returned no results")
                
        except Exception as e:
            print(f"  ❌ OpenAlex API Error: {e}")
    
    # Test Semantic Scholar API
    print("\n📬 Testing Semantic Scholar API...")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": "cinnamon insulin women",
                    "limit": 1,
                    "fields": "title,abstract,year,venue,externalIds"
                }
            )
            response.raise_for_status()
            data = response.json()
            papers = data.get("data", [])
            
            if papers:
                paper = papers[0]
                print(f"  ✅ Semantic Scholar works!")
                print(f"     Title: {paper.get('title', 'N/A')[:80]}...")
                print(f"     Year: {paper.get('year')}")
                ext_ids = paper.get('externalIds', {})
                print(f"     PMID: {ext_ids.get('PubMed', 'N/A')}")
            else:
                print("  ⚠️ Semantic Scholar returned no results")
                
        except Exception as e:
            print(f"  ❌ Semantic Scholar API Error: {e}")
    
    print("\n" + "=" * 60)
    print("🎯 TESTING COMPLETE PubMedService CLASS")
    print("=" * 60)
    
    # Now test the actual service class
    try:
        from app.services.pubmed_service import PubMedService, PUBMED_SEARCH_TOOL
        
        print(f"\n✅ Imports successful!")
        print(f"   Tool name: {PUBMED_SEARCH_TOOL['function']['name']}")
        
        service = PubMedService()
        
        for test in test_cases:
            print(f"\n🔍 Testing: {test['action_title']}")
            print(f"   Query: {test['query']}")
            
            result = await service.find_citation(
                query=test['query'],
                action_title=test['action_title'],
                category=test['category'],
                hormone=test['hormone'],
                db=None  # No caching for this test
            )
            
            if result and result.get("title"):
                print(f"   ✅ FOUND!")
                print(f"      Title: {result['title'][:60]}...")
                print(f"      Journal: {result['journal']}")
                print(f"      Year: {result['year']}")
                print(f"      PMID: {result.get('pmid', 'N/A')}")
                print(f"      Participants: {result.get('participants', 'N/A')}")
                print(f"      Source: {result.get('source', 'N/A')}")
                if result.get('finding'):
                    print(f"      Finding: {result['finding'][:100]}...")
            else:
                print(f"   ⚠️ No results found")
            
            await asyncio.sleep(0.5)  # Rate limit between tests
        
        await service.close()
        
    except ImportError as e:
        print(f"\n⚠️ Import failed (expected if running outside Docker): {e}")
        print("   The direct API tests above confirm the APIs work.")
    except Exception as e:
        print(f"\n❌ Service test error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("✅ ALL API TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_pubmed_service())
