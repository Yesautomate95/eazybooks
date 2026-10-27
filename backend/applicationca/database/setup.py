import psycopg2
from config.settings import settings

def main():
    # Connection details
    conn = psycopg2.connect(settings.DATABASE_URL)

    try:
        cursor = conn.cursor()

        # Enable UUID generation
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        # Execute SQL statements one by one
        sql_statements = [
            """
            CREATE TABLE IF NOT EXISTS CHATS (
                chat_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                client_id UUID NULL REFERENCES CLIENTS(client_id) ON DELETE SET NULL,
                lockr_id UUID NULL REFERENCES LOCKR(lockr_id) ON DELETE SET NULL,
                chat_name TEXT NOT NULL
                CHECK (
                    (client_id IS NOT NULL AND lockr_id IS NULL) OR
                    (client_id IS NULL AND lockr_id IS NOT NULL)
                )
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_chats_client ON FILES(client_id);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_chats_lockr ON FILES(lockr_id);
            """,
            """
            INSERT INTO CHATS (chat_name, lockr_id, client_id)
            SELECT file_name, lockr_id, client_id FROM FILES;

            """
        ]

        for sql in sql_statements:
            cursor.execute(sql)

        # # Inserting users
        # user_records = [
        #     ('alice',),
        #     ('bob',),
        #     ('charlie',)
        # ]
        # insert_user_query = "INSERT INTO USERS (username) VALUES (%s) RETURNING user_id;"
        # user_ids = []
        # for record in user_records:
        #     cursor.execute(insert_user_query, record)
        #     user_ids.append(cursor.fetchone()[0])
        #
        # # Inserting clients
        # client_records = [
        #     (user_ids[0], 'Client'), (user_ids[0], 'Client'), (user_ids[0], 'Client'), (user_ids[0], 'Client'),
        #     (user_ids[1], 'Client'), (user_ids[1], 'Client'), (user_ids[1], 'Client'), (user_ids[1], 'Client'),
        #     (user_ids[2], 'Client'), (user_ids[2], 'Client'), (user_ids[2], 'Client'), (user_ids[2], 'Client')
        # ]
        # insert_client_query = "INSERT INTO CLIENTS (user_id, name) VALUES (%s, %s) RETURNING client_id;"
        # client_ids = []
        # for record in client_records:
        #     cursor.execute(insert_client_query, record)
        #     client_ids.append(cursor.fetchone()[0])
        #
        # # Inserting lockrs
        # lockr_records = [
        #     (user_ids[0], 'Lockr A1'), (user_ids[0], 'Lockr A2'), (user_ids[0], 'Lockr A3'), (user_ids[0], 'Lockr A4'),
        #     (user_ids[1], 'Lockr B1'), (user_ids[1], 'Lockr B2'), (user_ids[1], 'Lockr B3'), (user_ids[1], 'Lockr B4'),
        #     (user_ids[2], 'Lockr C1'), (user_ids[2], 'Lockr C2'), (user_ids[2], 'Lockr C3'), (user_ids[2], 'Lockr C4')
        # ]
        # insert_lockr_query = "INSERT INTO LOCKR (user_id, name) VALUES (%s, %s) RETURNING lockr_id;"
        # lockr_ids = []
        # for record in lockr_records:
        #     cursor.execute(insert_lockr_query, record)
        #     lockr_ids.append(cursor.fetchone()[0])
        #
        # # Inserting files (batched for better performance)
        # file_records = [
        #     # Files associated with clients
        #     (client_ids[0], None, 'client_file_1.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (client_ids[0], None, 'client_file_2.docx', '{"size": "1MB", "type": "docx"}'),
        #     (client_ids[0], None, 'client_file_3.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (client_ids[0], None, 'client_file_4.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (client_ids[0], None, 'client_file_5.txt', '{"size": "100KB", "type": "txt"}'),
        #     (client_ids[1], None, 'client_file_6.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (client_ids[1], None, 'client_file_7.docx', '{"size": "2MB", "type": "docx"}'),
        #     (client_ids[1], None, 'client_file_8.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (client_ids[1], None, 'client_file_9.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (client_ids[1], None, 'client_file_10.txt', '{"size": "200KB", "type": "txt"}'),
        #     (client_ids[2], None, 'client_file_11.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (client_ids[2], None, 'client_file_12.docx', '{"size": "1MB", "type": "docx"}'),
        #     (client_ids[2], None, 'client_file_13.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (client_ids[2], None, 'client_file_14.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (client_ids[2], None, 'client_file_15.txt', '{"size": "100KB", "type": "txt"}'),
        #     (client_ids[3], None, 'client_file_16.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (client_ids[3], None, 'client_file_17.docx', '{"size": "2MB", "type": "docx"}'),
        #     (client_ids[3], None, 'client_file_18.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (client_ids[3], None, 'client_file_19.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (client_ids[3], None, 'client_file_20.txt', '{"size": "200KB", "type": "txt"}'),
        #     (client_ids[4], None, 'client_file_21.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (client_ids[4], None, 'client_file_22.docx', '{"size": "1MB", "type": "docx"}'),
        #     (client_ids[4], None, 'client_file_23.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (client_ids[4], None, 'client_file_24.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (client_ids[4], None, 'client_file_25.txt', '{"size": "100KB", "type": "txt"}'),
        #     (client_ids[5], None, 'client_file_26.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (client_ids[5], None, 'client_file_27.docx', '{"size": "2MB", "type": "docx"}'),
        #     (client_ids[5], None, 'client_file_28.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (client_ids[5], None, 'client_file_29.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (client_ids[5], None, 'client_file_30.txt', '{"size": "200KB", "type": "txt"}'),
        #     (client_ids[6], None, 'client_file_31.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (client_ids[6], None, 'client_file_32.docx', '{"size": "1MB", "type": "docx"}'),
        #     (client_ids[6], None, 'client_file_33.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (client_ids[6], None, 'client_file_34.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (client_ids[6], None, 'client_file_35.txt', '{"size": "100KB", "type": "txt"}'),
        #     (client_ids[7], None, 'client_file_36.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (client_ids[7], None, 'client_file_37.docx', '{"size": "2MB", "type": "docx"}'),
        #     (client_ids[7], None, 'client_file_38.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (client_ids[7], None, 'client_file_39.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (client_ids[7], None, 'client_file_40.txt', '{"size": "200KB", "type": "txt"}'),
        #     (client_ids[8], None, 'client_file_41.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (client_ids[8], None, 'client_file_42.docx', '{"size": "1MB", "type": "docx"}'),
        #     (client_ids[8], None, 'client_file_43.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (client_ids[8], None, 'client_file_44.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (client_ids[8], None, 'client_file_45.txt', '{"size": "100KB", "type": "txt"}'),
        #     (client_ids[9], None, 'client_file_46.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (client_ids[9], None, 'client_file_47.docx', '{"size": "2MB", "type": "docx"}'),
        #     (client_ids[9], None, 'client_file_48.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (client_ids[9], None, 'client_file_49.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (client_ids[9], None, 'client_file_50.txt', '{"size": "200KB", "type": "txt"}'),
        #     (client_ids[10], None, 'client_file_51.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (client_ids[10], None, 'client_file_52.docx', '{"size": "1MB", "type": "docx"}'),
        #     (client_ids[10], None, 'client_file_53.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (client_ids[10], None, 'client_file_54.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (client_ids[10], None, 'client_file_55.txt', '{"size": "100KB", "type": "txt"}'),
        #     (client_ids[11], None, 'client_file_56.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (client_ids[11], None, 'client_file_57.docx', '{"size": "2MB", "type": "docx"}'),
        #     (client_ids[11], None, 'client_file_58.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (client_ids[11], None, 'client_file_59.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (client_ids[11], None, 'client_file_60.txt', '{"size": "200KB", "type": "txt"}'),
        #
        #     # Files associated with lockrs
        #     (None, lockr_ids[0], 'lockr_file_1.pdf', '{"size": "4MB", "type": "pdf"}'),
        #     (None, lockr_ids[0], 'lockr_file_2.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[0], 'lockr_file_3.jpg', '{"size": "700KB", "type": "jpg"}'),
        #     (None, lockr_ids[0], 'lockr_file_4.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (None, lockr_ids[0], 'lockr_file_5.txt', '{"size": "150KB", "type": "txt"}'),
        #     (None, lockr_ids[1], 'lockr_file_6.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (None, lockr_ids[1], 'lockr_file_7.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[1], 'lockr_file_8.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (None, lockr_ids[1], 'lockr_file_9.xlsx', '{"size": "1.5MB", "type": "xlsx"}'),
        #     (None, lockr_ids[1], 'lockr_file_10.txt', '{"size": "120KB", "type": "txt"}'),
        #     (None, lockr_ids[2], 'lockr_file_11.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (None, lockr_ids[2], 'lockr_file_12.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[2], 'lockr_file_13.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (None, lockr_ids[2], 'lockr_file_14.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (None, lockr_ids[2], 'lockr_file_15.txt', '{"size": "150KB", "type": "txt"}'),
        #     (None, lockr_ids[3], 'lockr_file_16.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (None, lockr_ids[3], 'lockr_file_17.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[3], 'lockr_file_18.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (None, lockr_ids[3], 'lockr_file_19.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (None, lockr_ids[3], 'lockr_file_20.txt', '{"size": "120KB", "type": "txt"}'),
        #     (None, lockr_ids[4], 'lockr_file_21.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (None, lockr_ids[4], 'lockr_file_22.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[4], 'lockr_file_23.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (None, lockr_ids[4], 'lockr_file_24.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (None, lockr_ids[4], 'lockr_file_25.txt', '{"size": "100KB", "type": "txt"}'),
        #     (None, lockr_ids[5], 'lockr_file_26.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (None, lockr_ids[5], 'lockr_file_27.docx', '{"size": "2MB", "type": "docx"}'),
        #     (None, lockr_ids[5], 'lockr_file_28.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (None, lockr_ids[5], 'lockr_file_29.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (None, lockr_ids[5], 'lockr_file_30.txt', '{"size": "200KB", "type": "txt"}'),
        #     (None, lockr_ids[6], 'lockr_file_31.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (None, lockr_ids[6], 'lockr_file_32.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[6], 'lockr_file_33.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (None, lockr_ids[6], 'lockr_file_34.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (None, lockr_ids[6], 'lockr_file_35.txt', '{"size": "100KB", "type": "txt"}'),
        #     (None, lockr_ids[7], 'lockr_file_36.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (None, lockr_ids[7], 'lockr_file_37.docx', '{"size": "2MB", "type": "docx"}'),
        #     (None, lockr_ids[7], 'lockr_file_38.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (None, lockr_ids[7], 'lockr_file_39.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (None, lockr_ids[7], 'lockr_file_40.txt', '{"size": "200KB", "type": "txt"}'),
        #     (None, lockr_ids[8], 'lockr_file_41.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (None, lockr_ids[8], 'lockr_file_42.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[8], 'lockr_file_43.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (None, lockr_ids[8], 'lockr_file_44.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (None, lockr_ids[8], 'lockr_file_45.txt', '{"size": "100KB", "type": "txt"}'),
        #     (None, lockr_ids[9], 'lockr_file_46.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (None, lockr_ids[9], 'lockr_file_47.docx', '{"size": "2MB", "type": "docx"}'),
        #     (None, lockr_ids[9], 'lockr_file_48.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (None, lockr_ids[9], 'lockr_file_49.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (None, lockr_ids[9], 'lockr_file_50.txt', '{"size": "200KB", "type": "txt"}'),
        #     (None, lockr_ids[10], 'lockr_file_51.pdf', '{"size": "2MB", "type": "pdf"}'),
        #     (None, lockr_ids[10], 'lockr_file_52.docx', '{"size": "1MB", "type": "docx"}'),
        #     (None, lockr_ids[10], 'lockr_file_53.jpg', '{"size": "500KB", "type": "jpg"}'),
        #     (None, lockr_ids[10], 'lockr_file_54.xlsx', '{"size": "2MB", "type": "xlsx"}'),
        #     (None, lockr_ids[10], 'lockr_file_55.txt', '{"size": "100KB", "type": "txt"}'),
        #     (None, lockr_ids[11], 'lockr_file_56.pdf', '{"size": "3MB", "type": "pdf"}'),
        #     (None, lockr_ids[11], 'lockr_file_57.docx', '{"size": "2MB", "type": "docx"}'),
        #     (None, lockr_ids[11], 'lockr_file_58.jpg', '{"size": "600KB", "type": "jpg"}'),
        #     (None, lockr_ids[11], 'lockr_file_59.xlsx', '{"size": "1MB", "type": "xlsx"}'),
        #     (None, lockr_ids[11], 'lockr_file_60.txt', '{"size": "200KB", "type": "txt"}'),
        # ]
        #
        # insert_file_query = "INSERT INTO FILES (client_id, lockr_id, file_name, metadata) VALUES (%s, %s, %s, %s)"
        # cursor.executemany(insert_file_query, file_records)

        conn.commit()
        print("Database setup completed successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()