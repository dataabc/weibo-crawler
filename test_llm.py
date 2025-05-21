import json
import logging
from util.llm_analyzer import LLMAnalyzer

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    # 加载配置
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 初始化 LLM 分析器
    analyzer = LLMAnalyzer(config)
    
    # 测试文本
    test_text = """
    【法英加发表联合声明反对以色列扩大在加沙军事行动】法国、英国和加拿大19日发表联合声明，强烈反对以色列扩大在加沙地带的军事行动，呼吁以政府停止在加沙地带的军事行动，并立即允许人道主义援助进入。
　　法国总统府爱丽舍宫19日晚发布了《法国、英国和加拿大领导人关于加沙和西岸局势的联合声明》。声明说，以色列扩大军事行动造成加沙地带民众承受的苦难“令人无法忍受”。以政府拒绝向平民提供必要的人道主义援助是不可接受的，这可能违反了国际人道主义法。对加沙的毁灭性打击造成平民流离失所，违反了国际人道主义法。
　　声明表示，如果以色列不停止新一轮军事进攻并解除对人道主义援助的限制，三国将采取具体措施予以回应。
　　三国领导人在声明中表示，反对以色列任何扩大在约旦河西岸定居点的企图。以色列必须停止非法定居点建设，这些定居点破坏巴勒斯坦的生存环境，并危及以色列人和巴勒斯坦人的安全。
　　三国领导人表示，将继续与巴勒斯坦民族权力机构、地区伙伴、以色列和美国合作，以便在“阿拉伯方案”的基础上就加沙地带未来的安排达成共识。三国致力于承认巴勒斯坦国，这有助于实现“两国方案”，并准备为此与其他国家开展合作。
　　法国总统府未提及三国领导人发表此声明的背景。有法国媒体报道说，该声明针对的是以色列3月以来对加沙地带实施封锁并恢复密集军事打击，特别是以军5月18日宣布对加沙地带发动大规模地面行动后造成大量平民死伤。根据加沙地带卫生部门5月18日发布的数据，自3月18日以来，以色列方面对加沙地带多地发动袭击，已造成至少3193人死亡、8993人受伤。
    """
    
    # 测试情感分析
    print("\n=== 测试情感分析 ===")
    sentiment_result = analyzer.analyze_sentiment(test_text)
    print(f"情感分析结果: {sentiment_result}")
    
    # 测试摘要生成
    print("\n=== 测试摘要生成 ===")
    summary_result = analyzer.generate_summary(test_text)
    print(f"摘要生成结果: {summary_result}")
    
    # 测试异常检测
    print("\n=== 测试异常检测 ===")
    anomaly_result = analyzer.detect_anomaly(test_text)
    print(f"异常检测结果: {anomaly_result}")
    
    # 测试完整的微博分析
    print("\n=== 测试完整微博分析 ===")
    weibo_data = {
        "id": "test_001",
        "text": test_text,
        "user": "test_user",
        "created_at": "2024-03-20 10:00:00"
    }
    analysis_result = analyzer.analyze_weibo(weibo_data)
    print(f"完整分析结果: {json.dumps(analysis_result, ensure_ascii=False, indent=2)}")

if __name__ == "__main__":
    main()