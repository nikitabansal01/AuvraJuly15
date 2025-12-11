"""
Nutrition Expert - Specialized Diet & Nutrition Recommendations
================================================================

This expert module generates evidence-based nutrition recommendations
with sub-modules for specific hormone imbalances.

Sub-modules:
- InsulinResistanceDietModule: Low-GI, fiber-rich recommendations
- AndrogenReductionDietModule: Anti-androgen foods (spearmint, flaxseed)
- AntiInflammatoryDietModule: Inflammation-reducing nutrition
- ThyroidSupportDietModule: Iodine, selenium, thyroid-supporting foods
- EstrogenBalanceDietModule: Phytoestrogen balance
- CortisolDietModule: Stress-reducing nutrition
"""

from typing import List, Dict, Any, Optional
import asyncio
import logging

from app.services.recommendation_engine_v3.experts.base_expert import (
    BaseDomainExpert,
    BaseExpertSubModule,
    ExpertRecommendation
)
from app.services.recommendation_engine_v3.core.problem_narrower import FocusedProblem

logger = logging.getLogger(__name__)


# =============================================================================
# SUB-MODULES
# =============================================================================

class InsulinResistanceDietModule(BaseExpertSubModule):
    """
    Specialized module for insulin resistance focused nutrition.
    
    Target: Women with insulin resistance, weight gain, difficulty losing weight
    Evidence base: PCOS + insulin + diet studies from Pinecone RAG
    """
    
    MODULE_NAME = "insulin_resistance_diet"
    TARGET_ROOT_CAUSES = ['insulin_resistance', 'blood_sugar_instability', 'leptin_resistance']
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            "low glycemic index diet PCOS insulin resistance randomized",
            "dietary fiber insulin sensitivity polycystic ovary syndrome",
            "carbohydrate restriction metabolic outcomes PCOS women",
            "cinnamon supplementation blood glucose PCOS",
        ],
        'must_include_terms': ['insulin', 'glucose', 'glycemic', 'PCOS'],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    INTERVENTION_TEMPLATES = {
        'low_gi_diet': {
            'title': 'Low Glycemic Index Diet',
            'purpose': 'Stabilize blood sugar levels and improve insulin sensitivity',
            'action': 'Replace high-GI foods (white bread, white rice, sugary snacks) with low-GI alternatives (whole grains, legumes, non-starchy vegetables)',
            'specifics': [
                'Swap white rice for quinoa, brown rice, or cauliflower rice',
                'Choose whole grain bread over white bread',
                'Include protein with every meal to lower glycemic impact',
            ],
            'frequency': 'Every meal',
            'optimal_times': ['morning', 'afternoon', 'evening'],  # All meals
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
            'root_causes': ['insulin_resistance'],
        },
        'fiber_increase': {
            'title': 'High Fiber Intake',
            'purpose': 'Slow glucose absorption and improve insulin sensitivity',
            'action': 'Increase daily fiber intake to 25-30g through whole foods',
            'specifics': [
                'Add 2 tablespoons ground flaxseed daily',
                'Include legumes (beans, lentils) in at least one meal',
                'Consume 5+ servings of non-starchy vegetables',
            ],
            'food_amounts': ['2 tbsp', '1 cup', '5 servings'],
            'food_items': ['ground flaxseed', 'legumes', 'non-starchy vegetables'],
            'frequency': 'Daily',
            'optimal_times': ['morning'],  # Best taken in morning
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '4-8 weeks',
            'root_causes': ['insulin_resistance'],
        },
        'cinnamon_supplementation': {
            'title': 'Cinnamon Supplementation',
            'purpose': 'Support healthy blood sugar levels naturally',
            'action': 'Consume 1-2g of Ceylon cinnamon daily',
            'specifics': [
                'Add 1/2 teaspoon Ceylon cinnamon to morning oatmeal or smoothie',
                'Use Ceylon (not Cassia) cinnamon to avoid coumarin toxicity',
            ],
            'food_amounts': ['1-2g', '1/2 tsp'],
            'food_items': ['Ceylon cinnamon'],
            'frequency': 'Daily',
            'optimal_times': ['morning'],  # Best with breakfast
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '8-12 weeks',
            'contraindications': ['Avoid high doses during pregnancy'],
            'root_causes': ['insulin_resistance', 'blood_sugar_instability'],
        },
        'reduce_refined_carbs': {
            'title': 'Reduce Refined Carbohydrates',
            'purpose': 'Lower insulin spikes and reduce insulin resistance',
            'action': 'Limit refined carbohydrates to less than 25% of daily calories',
            'specifics': [
                'Limit added sugars to <25g per day',
                'Avoid white flour products',
                'Choose complex carbs over simple carbs',
            ],
            'frequency': 'Daily',
            'optimal_times': ['morning', 'afternoon', 'evening'],  # All meals
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
            'root_causes': ['insulin_resistance'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate insulin resistance diet recommendations with real RAG evidence"""
        logger.info(f"🍽️ {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Step 1: Retrieve real evidence from Pinecone
        retrieved_docs = await self.retrieve_evidence(
            topic="insulin resistance diet PCOS",
            focused_problem=focused_problem
        )
        citations = self._extract_citations(retrieved_docs)
        logger.info(f"📚 {self.MODULE_NAME}: Found {len(citations)} citations from research")
        
        # Step 2: Generate from templates with real citations
        for template_key in ['low_gi_diet', 'fiber_increase', 'reduce_refined_carbs']:
            # Find relevant citations for this specific recommendation
            rec_citations = self._match_citations_to_template(template_key, citations)
            rec = self._create_recommendation(template_key, focused_problem, citations=rec_citations)
            if rec:
                recommendations.append(rec)
        
        # Add cinnamon if no contraindications
        if not any('pregnancy' in str(c.description).lower() for c in focused_problem.constraints):
            cinnamon_citations = self._match_citations_to_template('cinnamon_supplementation', citations)
            rec = self._create_recommendation('cinnamon_supplementation', focused_problem, citations=cinnamon_citations)
            if rec:
                recommendations.append(rec)
        
        # Calculate relevance score based on primary concern
        primary = focused_problem.primary_concern
        for rec in recommendations:
            if primary.concern_type in ['weight_gain', 'difficulty_losing_weight']:
                rec['relevance_score'] = 1.0
            elif 'insulin' in str(primary.root_causes):
                rec['relevance_score'] = 0.9
            else:
                rec['relevance_score'] = 0.6
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations
    
    def _match_citations_to_template(self, template_key: str, citations: List[Dict]) -> List[Dict]:
        """Match relevant citations to a specific template based on keywords"""
        template = self.INTERVENTION_TEMPLATES.get(template_key, {})
        keywords = {
            'low_gi_diet': ['glycemic', 'low-gi', 'low gi', 'glycaemic'],
            'fiber_increase': ['fiber', 'fibre', 'psyllium', 'dietary fiber'],
            'cinnamon_supplementation': ['cinnamon', 'cinnamomum'],
            'reduce_refined_carbs': ['carbohydrate', 'refined', 'sugar', 'glucose'],
        }
        
        search_terms = keywords.get(template_key, [template_key])
        matched = []
        for c in citations:
            title = str(c.get('title', '')).lower()
            if any(term.lower() in title for term in search_terms):
                matched.append(c)
        
        # Return matched or first 2 general citations
        return matched[:3] if matched else citations[:2]


class AndrogenReductionDietModule(BaseExpertSubModule):
    """
    Specialized module for androgen reduction through diet.
    
    Target: Women with high androgens, acne, hirsutism, hair loss
    Evidence base: Anti-androgen foods and supplements from Pinecone RAG
    """
    
    MODULE_NAME = "androgen_reduction_diet"
    TARGET_ROOT_CAUSES = ['androgen_high', 'hirsutism', 'acne']
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            "spearmint tea testosterone PCOS women clinical",
            "flaxseed lignans androgen PCOS randomized",
            "anti-androgen diet polycystic ovary syndrome",
            "omega-3 inflammation PCOS hormones",
        ],
        'must_include_terms': ['androgen', 'testosterone', 'PCOS', 'hormone'],
        'study_type_preference': ['meta-analysis', 'RCT', 'clinical trial'],
        'min_evidence_level': 'moderate',
    }
    
    INTERVENTION_TEMPLATES = {
        'spearmint_tea': {
            'title': 'Spearmint Tea',
            'purpose': 'Natural anti-androgen effect to reduce testosterone levels',
            'action': 'Drink 2 cups of spearmint tea daily',
            'specifics': [
                'Steep 1 tablespoon dried spearmint in hot water for 5-10 minutes',
                'Drink one cup in morning and one in evening',
            ],
            'food_amounts': ['2 cups', '1 tbsp'],
            'food_items': ['spearmint tea'],
            'frequency': 'Daily',
            'frequency_detail': 'daily:2',
            'optimal_times': ['morning', 'evening'],  # Twice daily
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': '4-6 weeks for initial effects',
            'root_causes': ['androgen_high'],
        },
        'flaxseed_lignans': {
            'title': 'Ground Flaxseed',
            'purpose': 'Lignans in flaxseed help reduce free testosterone',
            'action': 'Consume 2 tablespoons of freshly ground flaxseed daily',
            'specifics': [
                'Grind whole flaxseeds fresh for maximum benefit',
                'Add to smoothies, yogurt, or oatmeal',
                'Store ground flaxseed in refrigerator',
            ],
            'food_amounts': ['2 tbsp'],
            'food_items': ['ground flaxseed'],
            'frequency': 'Daily',
            'optimal_times': ['morning'],  # Best with breakfast
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': '8-12 weeks',
            'root_causes': ['androgen_high', 'estrogen_balance'],
        },
        'anti_inflammatory_omega3': {
            'title': 'Omega-3 Rich Foods',
            'purpose': 'Reduce inflammation associated with high androgens',
            'action': 'Consume omega-3 rich foods 3-4 times per week',
            'specifics': [
                'Eat fatty fish (salmon, mackerel, sardines) 2-3 times per week',
                'Add walnuts or chia seeds daily',
                'Consider algae-based omega-3 if vegetarian',
            ],
            'food_amounts': ['100-150g', '1 oz', '1 tbsp'],
            'food_items': ['fatty fish', 'walnuts', 'chia seeds'],
            'frequency': 'weekly:3',
            'optimal_times': ['afternoon', 'evening'],  # Lunch/dinner meals
            'priority': 'medium',
            'evidence_strength': 'moderate',
            'timeline': '8-12 weeks',
            'root_causes': ['inflammation', 'androgen_high'],
        },
        'reduce_dairy': {
            'title': 'Reduce Dairy Intake',
            'purpose': 'Dairy may contain hormones that can worsen androgen levels',
            'action': 'Replace cow dairy with plant-based alternatives',
            'specifics': [
                'Switch to almond, oat, or coconut milk',
                'Limit cheese to occasional consumption',
                'Choose dairy-free yogurt alternatives',
            ],
            'frequency': 'Daily',
            'optimal_times': ['morning', 'afternoon', 'evening'],  # All meals
            'priority': 'medium',
            'evidence_strength': 'weak',
            'timeline': '4-8 weeks',
            'root_causes': ['androgen_high', 'acne'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate androgen reduction diet recommendations with real RAG evidence"""
        logger.info(f"🍽️ {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Step 1: Retrieve real evidence from Pinecone
        retrieved_docs = await self.retrieve_evidence(
            topic="androgen reduction diet PCOS",
            focused_problem=focused_problem
        )
        citations = self._extract_citations(retrieved_docs)
        logger.info(f"📚 {self.MODULE_NAME}: Found {len(citations)} citations from research")
        
        # Step 2: Generate from templates with real citations
        for template_key, template in self.INTERVENTION_TEMPLATES.items():
            # Check for dairy constraint before recommending dairy reduction
            if template_key == 'reduce_dairy':
                if any('dairy' in c.description.lower() for c in focused_problem.constraints):
                    continue  # Already avoiding dairy
            
            rec_citations = self._match_citations_to_template(template_key, citations)
            rec = self._create_recommendation(template_key, focused_problem, citations=rec_citations)
            if rec:
                recommendations.append(rec)
        
        # Calculate relevance
        primary = focused_problem.primary_concern
        for rec in recommendations:
            if primary.concern_type in ['acne', 'hirsutism', 'hair_loss']:
                rec['relevance_score'] = 1.0
            elif 'androgen' in str(primary.root_causes):
                rec['relevance_score'] = 0.9
            else:
                rec['relevance_score'] = 0.5
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations
    
    def _match_citations_to_template(self, template_key: str, citations: List[Dict]) -> List[Dict]:
        """Match relevant citations to a specific template"""
        keywords = {
            'spearmint_tea': ['spearmint', 'mentha spicata', 'tea', 'herbal'],
            'flaxseed_lignans': ['flaxseed', 'linseed', 'lignans', 'flax'],
            'anti_inflammatory_omega3': ['omega-3', 'fish oil', 'EPA', 'DHA', 'fatty acid'],
            'reduce_dairy': ['dairy', 'milk', 'lactose', 'IGF-1'],
        }
        
        search_terms = keywords.get(template_key, [template_key])
        matched = []
        for c in citations:
            title = str(c.get('title', '')).lower()
            if any(term.lower() in title for term in search_terms):
                matched.append(c)
        
        return matched[:3] if matched else citations[:2]


class AntiInflammatoryDietModule(BaseExpertSubModule):
    """
    Specialized module for anti-inflammatory nutrition.
    
    Target: Women with inflammation, painful periods, endometriosis
    Evidence base: Anti-inflammatory interventions from Pinecone RAG
    """
    
    MODULE_NAME = "anti_inflammatory_diet"
    TARGET_ROOT_CAUSES = ['inflammation', 'prostaglandin_imbalance']
    
    RETRIEVAL_CONFIG = {
        'primary_queries': [
            "Mediterranean diet inflammation PCOS women",
            "turmeric curcumin anti-inflammatory polycystic ovary",
            "anti-inflammatory diet chronic inflammation women",
            "omega-3 prostaglandins PCOS dysmenorrhea",
        ],
        'must_include_terms': ['inflammation', 'anti-inflammatory', 'PCOS'],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    INTERVENTION_TEMPLATES = {
        'turmeric_curcumin': {
            'title': 'Turmeric/Curcumin',
            'purpose': 'Powerful anti-inflammatory compound to reduce systemic inflammation',
            'action': 'Consume turmeric with black pepper daily',
            'specifics': [
                'Take 500-1000mg curcumin supplement with piperine',
                'Or add 1 tsp turmeric + pinch black pepper to food',
                'Best absorbed with fat (coconut oil, olive oil)',
            ],
            'food_amounts': ['500-1000mg', '1 tsp'],
            'food_items': ['turmeric', 'black pepper'],
            'frequency': 'Daily',
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '4-8 weeks',
            'contraindications': ['May interact with blood thinners'],
            'root_causes': ['inflammation'],
        },
        'anti_inflammatory_diet': {
            'title': 'Mediterranean-Style Diet',
            'purpose': 'Reduce chronic inflammation through whole foods',
            'action': 'Follow Mediterranean diet principles',
            'specifics': [
                'Base meals on vegetables, fruits, whole grains',
                'Use olive oil as primary fat',
                'Eat fish 2-3 times per week',
                'Limit red meat to 1-2 times per week',
            ],
            'frequency': 'Daily',
            'priority': 'high',
            'evidence_strength': 'strong',
            'timeline': '8-12 weeks',
            'root_causes': ['inflammation'],
        },
        'eliminate_processed_foods': {
            'title': 'Eliminate Processed Foods',
            'purpose': 'Remove pro-inflammatory foods from diet',
            'action': 'Eliminate ultra-processed foods and refined seed oils',
            'specifics': [
                'Avoid packaged snacks, fast food',
                'Replace seed oils with olive oil, avocado oil',
                'Read labels - avoid added sugars and artificial ingredients',
            ],
            'frequency': 'Daily',
            'optimal_times': ['morning', 'afternoon', 'evening'],  # All meals
            'priority': 'high',
            'evidence_strength': 'moderate',
            'timeline': '4-6 weeks',
            'root_causes': ['inflammation'],
        },
    }
    
    async def generate(self, focused_problem: FocusedProblem) -> List[Dict[str, Any]]:
        """Generate anti-inflammatory diet recommendations with real RAG evidence"""
        logger.info(f"🍽️ {self.MODULE_NAME}: Generating recommendations...")
        
        recommendations = []
        
        # Step 1: Retrieve real evidence from Pinecone
        retrieved_docs = await self.retrieve_evidence(
            topic="anti-inflammatory diet PCOS",
            focused_problem=focused_problem
        )
        citations = self._extract_citations(retrieved_docs)
        logger.info(f"📚 {self.MODULE_NAME}: Found {len(citations)} citations from research")
        
        # Step 2: Generate from templates with real citations
        for template_key, template in self.INTERVENTION_TEMPLATES.items():
            rec_citations = self._match_citations_to_template(template_key, citations)
            rec = self._create_recommendation(template_key, focused_problem, citations=rec_citations)
            if rec:
                recommendations.append(rec)
        
        # Calculate relevance
        primary = focused_problem.primary_concern
        for rec in recommendations:
            if primary.concern_type in ['painful_periods', 'bloating']:
                rec['relevance_score'] = 1.0
            elif 'inflammation' in str(primary.root_causes):
                rec['relevance_score'] = 0.9
            else:
                rec['relevance_score'] = 0.5
        
        logger.info(f"✅ {self.MODULE_NAME}: Generated {len(recommendations)} recommendations")
        return recommendations
    
    def _match_citations_to_template(self, template_key: str, citations: List[Dict]) -> List[Dict]:
        """Match relevant citations to a specific template"""
        keywords = {
            'turmeric_curcumin': ['turmeric', 'curcumin', 'curcuminoids'],
            'anti_inflammatory_diet': ['mediterranean', 'diet', 'anti-inflammatory'],
            'eliminate_processed_foods': ['processed', 'ultra-processed', 'refined'],
        }
        
        search_terms = keywords.get(template_key, [template_key])
        matched = []
        for c in citations:
            title = str(c.get('title', '')).lower()
            if any(term.lower() in title for term in search_terms):
                matched.append(c)
        
        return matched[:3] if matched else citations[:2]


# =============================================================================
# MAIN NUTRITION EXPERT
# =============================================================================

class NutritionExpert(BaseDomainExpert):
    """
    Nutrition Domain Expert
    
    Generates evidence-based nutrition recommendations using specialized
    sub-modules for different hormone imbalances with real RAG retrieval.
    """
    
    DOMAIN_NAME = "nutrition"
    
    def _initialize_submodules(self):
        """Initialize nutrition sub-modules"""
        self.submodules = {
            'insulin_resistance_diet': InsulinResistanceDietModule(),
            'androgen_reduction_diet': AndrogenReductionDietModule(),
            'anti_inflammatory_diet': AntiInflammatoryDietModule(),
            # Add more sub-modules as needed:
            # 'thyroid_support_diet': ThyroidSupportDietModule(),
            # 'estrogen_balance_diet': EstrogenBalanceDietModule(),
            # 'cortisol_diet': CortisolDietModule(),
        }
        # Pass retrieval to submodules if available
        if self.retrieval:
            for submodule in self.submodules.values():
                submodule.set_retrieval(self.retrieval)
    
    async def generate_recommendations(
        self,
        focused_problem: FocusedProblem,
        active_submodules: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate nutrition recommendations from relevant sub-modules.
        """
        logger.info(f"🥗 NutritionExpert: Starting recommendation generation")
        logger.info(f"   Primary concern: {focused_problem.primary_concern.concern_type}")
        logger.info(f"   Root causes: {focused_problem.get_all_root_causes()}")
        
        # Select which sub-modules to run
        selected_modules = self._select_submodules(focused_problem, active_submodules)
        logger.info(f"   Selected modules: {[m.MODULE_NAME for m in selected_modules]}")
        
        # Run sub-modules in parallel
        tasks = [module.generate(focused_problem) for module in selected_modules]
        module_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle errors and merge results
        all_recommendations = []
        for module, result in zip(selected_modules, module_results):
            if isinstance(result, Exception):
                logger.error(f"❌ {module.MODULE_NAME} failed: {result}")
            else:
                all_recommendations.extend(result)
        
        # Merge and deduplicate
        merged = self._merge_recommendations([all_recommendations])
        
        logger.info(f"✅ NutritionExpert: Generated {len(merged)} total recommendations")
        
        return merged
