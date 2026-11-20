# TaxOntOy

[otl-relations.json](otl-relations.json) は OnTautoLogy の第一弾（監査）、第二弾（概念）、第三弾（事例）の関係を、分析したい意味に対応づける最小の分類表です。キーは OTL のグラフ JSON に現れる関係名です。元の辺の向きと ID を保持します。

第二弾では上位から下位へ向かう `narrower` と、相互に張られる `similarTo` を別々に集計します。第三弾では候補、人の判断記録、肯定、否定を別々に数えます。未登録の関係は元のグラフに残ります。TopOntoLogy の `topology.py` が分類ごとの構造を集計し、OnTautoLogy が事例の判定を担当します。
