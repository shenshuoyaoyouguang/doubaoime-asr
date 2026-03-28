"""
演示如何调用 NER (Named Entity Recognition) 接口

标注文本中的实体，理论上可以自行开发相关的优化功能，用于增强文本准确性
"""
from doubaoime_asr import ASRConfig, ner

def main():
    config = ASRConfig(
        credential_path="./credentials.json",
        )

    ner_results = ner(config, "张三李四以及张三在使用 Chrome 浏览器")
    # 输出：results=[
    #     NerResult(
    #       text='张三李四以及张三在使用 Chrome 浏览器',
    #       words=[
    #           NerWord(freq=2, word='张三'), NerWord(freq=1, word='李四'), 
    #           NerWord(freq=1, word='Chrome 浏览器'), NerWord(freq=1, word='张三李四'), 
    #           NerWord(freq=1, word='Chrome')
    #       ]
    #     )
    # ]
    print(ner_results)


if __name__ == "__main__":
    main()
