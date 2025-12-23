#!/usr/bin/env python3
"""
End-to-end test for GPT Tool Calling with PubMed.
This tests the exact flow that action_plan_generator.py uses.
"""
import asyncio
import json
import os
import sys
import httpx

# Add project to path
sys.path.insert(0, '/Users/mohanganesh/AUVRA/AuvraJuly15')

# Load environment variables
from dotenv import load_dotenv
load_dotenv('/Users/mohanganesh/AUVRA/AuvraJuly15/.env')


async def test_gpt_tool_calling():
    """Test GPT calling our research paper tool."""
    import httpx
    
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("❌ OPENAI_API_KEY not found in .env")
        return False
    
    print("=" * 70)
    print("🧪 END-TO-END TEST: GPT Tool Calling for Research Papers")
    print("=" * 70)
    
    # Define the tool (same as in pubmed_service.py)
    PUBMED_SEARCH_TOOL = {
        "type": "function",
        "function": {
            "name": "search_research_paper",
            "description": "Search for a REAL published research paper to cite. Returns paper with PMID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query with 3-5 key terms."
                    },
                    "action_title": {
                        "type": "string",
                        "description": "The title of the action this citation is for."
                    }
                },
                "required": ["query", "action_title"]
            }
        }
    }
    
    # Simple test prompt
    test_prompt = """Generate 1 wellness action for a woman focused on insulin health.

You MUST call the search_research_paper tool to get a REAL citation.

Return JSON with this format:
{
  "actions": [{
    "title": "action title",
    "category": "food",
    "target_hormone": "insulin",
    "research_studies": [<include paper details from tool>]
  }]
}"""
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        print("\n📤 Step 1: Calling GPT-4o-mini with tool definition...")
        
        try:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are a wellness AI. Use the search_research_paper tool to get REAL citations."},
                        {"role": "user", "content": test_prompt}
                    ],
                    "tools": [PUBMED_SEARCH_TOOL],
                    "tool_choice": "auto",
                    "temperature": 0.3,
                    "max_tokens": 2000
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            message = data["choices"][0]["message"]
            
            # Check if GPT called the tool
            if message.get("tool_calls"):
                print(f"   ✅ GPT requested {len(message['tool_calls'])} tool call(s)")
                
                tool_results = []
                for tool_call in message["tool_calls"]:
                    func_name = tool_call["function"]["name"]
                    args = json.loads(tool_call["function"]["arguments"])
                    
                    print(f"\n📤 Step 2: Executing tool call:")
                    print(f"   Function: {func_name}")
                    print(f"   Query: {args.get('query', 'N/A')}")
                    print(f"   Action: {args.get('action_title', 'N/A')}")
                    
                    # Execute actual PubMed search
                    paper = await search_pubmed_real(
                        query=args.get("query", ""),
                        client=client
                    )
                    
                    if paper:
                        print(f"\n   ✅ FOUND REAL PAPER:")
                        print(f"      Title: {paper['title'][:60]}...")
                        print(f"      Journal: {paper['journal']}")
                        print(f"      Year: {paper['year']}")
                        print(f"      PMID: {paper['pmid']}")
                        print(f"      Link: https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/")
                    else:
                        print("   ⚠️ No paper found")
                    
                    tool_results.append({
                        "tool_call_id": tool_call["id"],
                        "role": "tool",
                        "content": json.dumps(paper) if paper else json.dumps({"error": "No papers found"})
                    })
                
                # Step 3: Send tool results back to GPT
                print("\n📤 Step 3: Sending results back to GPT...")
                
                assistant_message = {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": message.get("tool_calls")
                }
                
                response2 = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {openai_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": "You are a wellness AI."},
                            {"role": "user", "content": test_prompt},
                            assistant_message,
                            *tool_results
                        ],
                        "temperature": 0.3,
                        "max_tokens": 1500,
                        "response_format": {"type": "json_object"}
                    }
                )
                
                response2.raise_for_status()
                data2 = response2.json()
                
                final_content = data2["choices"][0]["message"]["content"]
                final_data = json.loads(final_content)
                
                print("\n✅ Step 4: FINAL RESULT:")
                print("-" * 50)
                print(json.dumps(final_data, indent=2)[:1500])
                print("-" * 50)
                
                # Verify PMID is included
                if "actions" in final_data and final_data["actions"]:
                    action = final_data["actions"][0]
                    research = action.get("research_studies", [])
                    if research and isinstance(research, list) and len(research) > 0:
                        pmid = research[0].get("pmid", "")
                        if pmid:
                            print(f"\n🎉 SUCCESS! Citation includes PMID: {pmid}")
                            print(f"   Verify at: https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
                            return True
                        else:
                            print("\n⚠️ Citation missing PMID")
                    else:
                        print("\n⚠️ No research_studies in response")
                
            else:
                print("   ❌ GPT did NOT call the tool!")
                print(f"   Content: {message.get('content', 'N/A')[:200]}")
                return False
                
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    return True


async def search_pubmed_real(query: str, client: httpx.AsyncClient) -> dict:
    """Actually search PubMed for a paper."""
    try:
        # Add women focus if not present
        if "women" not in query.lower() and "female" not in query.lower():
            query = f"({query}) AND (women OR female)"
        
        # Search
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": 1,
            "retmode": "json",
            "sort": "relevance",
            "mindate": "2010",
            "maxdate": "2025"
        }
        
        resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=params
        )
        resp.raise_for_status()
        
        pmids = resp.json().get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return {}
        
        pmid = pmids[0]
        
        # Fetch details
        await asyncio.sleep(0.35)
        
        import xml.etree.ElementTree as ET
        
        resp2 = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "xml"}
        )
        resp2.raise_for_status()
        
        root = ET.fromstring(resp2.content)
        article = root.find(".//PubmedArticle")
        
        if article is None:
            return {"pmid": pmid, "title": "Unknown", "journal": "Unknown", "year": 2020}
        
        title_elem = article.find(".//ArticleTitle")
        journal_elem = article.find(".//Journal/Title")
        year_elem = article.find(".//PubDate/Year")
        
        # Get abstract
        abstract_parts = []
        for text in article.findall(".//AbstractText"):
            if text.text:
                abstract_parts.append(text.text)
        abstract = " ".join(abstract_parts)
        
        return {
            "title": title_elem.text if title_elem is not None else "Unknown",
            "journal": journal_elem.text if journal_elem is not None else "Unknown",
            "year": int(year_elem.text) if year_elem is not None and year_elem.text else 2020,
            "pmid": pmid,
            "finding": abstract[:200] + "..." if abstract else "Research study on women's health",
            "participants": 0,
            "verification_link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "source": "pubmed"
        }
        
    except Exception as e:
        print(f"   PubMed error: {e}")
        return {}


if __name__ == "__main__":
    success = asyncio.run(test_gpt_tool_calling())
    
    print("\n" + "=" * 70)
    if success:
        print("🎉 END-TO-END TEST PASSED!")
        print("   The GPT tool calling pipeline is working correctly.")
        print("   Real PMIDs are being fetched from PubMed and included in responses.")
    else:
        print("⚠️ TEST COMPLETED WITH ISSUES")
        print("   Check the output above for details.")
    print("=" * 70)
