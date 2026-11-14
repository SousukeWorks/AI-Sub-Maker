"""Song titles only, checked against AKB48's official current stage index.

https://www.akb48.co.jp/about/song/ (2026-11-13). No song lyrics bundled.
"""
CATALOG = [
 ('stage-72', 'ここからだ', '''overture 2.0|ここからだ|恋愛カンニング|ロマンティック男爵|劇場へ ようこそ！|Lollipop|風の待ち伏せ|クリスマスリング|２月のMermaid|振り向きざまのキッス|夜中過ぎのアウトロー|奇跡が消えても|シクラメンが咲く頃|まだ見たことのない景色へ|緞帳を上げてくれ！|そんなに好きだったら|Hungry love'''),
 ('stage-73', 'RESET', '''overture 2.0|RESET|洗濯物たち|彼女になれますか？|ウッホウッホホ|制服レジスタンス|奇跡は間に合わない|逆転王子様|明日のためにキスを|心の端のソファー|毒蜘蛛|オケラ|ホワイトデーには…|ジグソーパズル４８|星空のミステイク|夢の鐘|引っ越しました'''),
 ('stage-69', 'そこに未来はある', '''overture 2.0|RIVER|最終ベルが鳴る|Waiting room|知ったかぶりのその下に|エンドロール|Bye Bye Bye|愛しさを丸めて|Stoicな美学|全力反抗期|初恋ドア|抱きしめちゃいけない|君に会うたび 恋をする|見えない空はいつでも青い|あの夏の防波堤|大声ダイヤモンド|言い訳Maybe|君のことが好きだから'''),
 ('stage-74', '手をつなぎながら', '''overture 2.0|僕らの風|マンゴーNo.2|手をつなぎながら|チャイムはLOVE SONG|Glory days|この胸のバーコード|ウィンブルドンへ連れて行って|雨のピアニスト|チョコの行方|Innocence|ロマンスロケット|恋の傾向と対策|大好き|ロープの友情|火曜日の夜、水曜日の朝|遠くにいても'''),
 ('stage-75', '夢のポップスター', '''overture 2.0|憧れのポップスター|偶然の十字路|必殺テレポート|Get you!|涙の湘南|最初の愛の物語|思い出す度につらくなる|君はペガサス|君と僕の関係|109(マルキュー)|イチニノサン|初恋は実らない|あなたがいてくれたから|金の羽根を持つ人よ|To be continued.|やさしさの地図'''),
]


def initial_library():
    return {'schema': 1, 'catalog_checked': '2026-11-13',
            'stages': [{'id': key, 'name': name,
                        'source': f'https://www.akb48.co.jp/about/song/list/{key}/',
                        'songs': [{'id': f'{key}-{i}', 'title': title, 'japanese': '', 'chinese': ''}
                                  for i, title in enumerate(titles.split('|'))]}
                       for key, name, titles in CATALOG]}
