from typing import Dict, List, Tuple

class RootCauseEngine:
    """
    Hormone imbalance root cause analysis engine
    Currently returns hardcoded results
    """
    
    @staticmethod
    def analyze_hormone_imbalance(user_data: Dict) -> Dict[str, any]:
        """
        Analyze hormone imbalance based on user data
        Currently returns hardcoded results
        
        Args:
            user_data: User survey data
            
        Returns:
            Dict containing:
            - primary_imbalance: Primary hormone imbalance (e.g., "progesterone")
            - primary_level: Primary hormone level (e.g., "low")
            - secondary_imbalances: List of secondary hormone imbalances (e.g., ["testosterone"])
            - secondary_levels: List of secondary hormone levels (e.g., ["low"])
        """
        # Hardcoded results
        return {
            "primary_imbalance": "progesterone",
            "primary_level": "low",
            "secondary_imbalances": ["testosterone"],
            "secondary_levels": ["low"]
        }
    
    @staticmethod
    def get_formatted_imbalance_text(analysis_result: Dict) -> str:
        """
        Format analysis result into text for prompts
        
        Args:
            analysis_result: Result from analyze_hormone_imbalance
            
        Returns:
            Formatted text (e.g., "progesterone (low), Secondary: testosterone (low)")
        """
        primary = f"{analysis_result['primary_imbalance']} ({analysis_result['primary_level']})"
        
        if analysis_result['secondary_imbalances']:
            secondary_parts = []
            for i, hormone in enumerate(analysis_result['secondary_imbalances']):
                level = analysis_result['secondary_levels'][i] if i < len(analysis_result['secondary_levels']) else "unknown"
                secondary_parts.append(f"{hormone} ({level})")
            secondary_text = f", Secondary: {', '.join(secondary_parts)}"
        else:
            secondary_text = ""
            
        return f"{primary}{secondary_text}"
    
    @staticmethod
    def get_related_hormones(analysis_result: Dict) -> List[str]:
        """
        Extract related hormones from analysis result
        
        Args:
            analysis_result: Result from analyze_hormone_imbalance
            
        Returns:
            List of related hormones (e.g., ["progesterone", "testosterone"])
        """
        hormones = [analysis_result['primary_imbalance']]
        hormones.extend(analysis_result['secondary_imbalances'])
        return hormones
