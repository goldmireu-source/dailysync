import sqlite3

conn = sqlite3.connect('data/app.db')
c = conn.cursor()

# "null" 문자열로 저장된 것 확인
c.execute("SELECT COUNT(*) FROM articles WHERE embedding = 'null'")
print('null 문자열 기사:', c.fetchone()[0])

c.execute("SELECT COUNT(*) FROM papers WHERE embedding = 'null'")
print('null 문자열 논문:', c.fetchone()[0])

# NULL로 초기화
c.execute("UPDATE articles SET embedding = NULL WHERE embedding = 'null'")
c.execute("UPDATE papers SET embedding = NULL WHERE embedding = 'null'")
conn.commit()

# 결과 확인
c.execute("SELECT COUNT(*) FROM articles WHERE embedding IS NULL")
print('초기화된 기사:', c.fetchone()[0])

c.execute("SELECT COUNT(*) FROM papers WHERE embedding IS NULL")
print('초기화된 논문:', c.fetchone()[0])

conn.close()
print('완료')