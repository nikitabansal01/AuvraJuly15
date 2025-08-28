from typing import Dict, List, Tuple

class RootCauseEngine:
    """
    호르몬 불균형 root cause 분석 엔진
    현재는 하드코딩된 결과를 반환
    """
    
    @staticmethod
    def analyze_hormone_imbalance(user_data: Dict) -> Dict[str, any]:
        """
        사용자 데이터를 기반으로 호르몬 불균형을 분석
        현재는 하드코딩된 결과 반환
        
        Args:
            user_data: 사용자 설문 데이터
            
        Returns:
            Dict containing:
            - primary_imbalance: 주요 호르몬 불균형 (예: "progesterone")
            - primary_level: 주요 호르몬 수준 (예: "low")
            - secondary_imbalances: 보조 호르몬 불균형 리스트 (예: ["testosterone"])
            - secondary_levels: 보조 호르몬 수준 리스트 (예: ["low"])
        """
        # 하드코딩된 결과
        return {
            "primary_imbalance": "progesterone",
            "primary_level": "low",
            "secondary_imbalances": ["testosterone"],
            "secondary_levels": ["low"]
        }
    
    @staticmethod
    def get_formatted_imbalance_text(analysis_result: Dict) -> str:
        """
        분석 결과를 프롬프트용 텍스트로 포맷팅
        
        Args:
            analysis_result: analyze_hormone_imbalance의 결과
            
        Returns:
            포맷팅된 텍스트 (예: "progesterone (low), Secondary: testosterone (low)")
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
        분석 결과에서 관련 호르몬들만 추출
        
        Args:
            analysis_result: analyze_hormone_imbalance의 결과
            
        Returns:
            관련 호르몬 리스트 (예: ["progesterone", "testosterone"])
        """
        hormones = [analysis_result['primary_imbalance']]
        hormones.extend(analysis_result['secondary_imbalances'])
        return hormones
