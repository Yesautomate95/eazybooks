# Database Structure

This document describes the structure of all tables in the database.

## chats

### Columns

| Column Name | Data Type | Nullable | Default | Max Length |
|------------|-----------|----------|----------|------------|
| chat_id | character varying | NO | None | 255 |
| client_id | uuid | YES | None | N/A |
| lockr_id | uuid | YES | None | N/A |
| user_id | character varying | YES | None | 255 |
| chat_description | text | YES | None | N/A |
| chat_date | timestamp without time zone | YES | None | N/A |
| chat_name | text | YES | None | N/A |
| chat_type | text | YES | None | N/A |

### Constraints

- **chats_client_id_fkey**: FOREIGN KEY (client_id) REFERENCES clients(client_id)
- **chats_lockr_id_fkey**: FOREIGN KEY (lockr_id) REFERENCES lockr(lockr_id)
- **chats_pkey**: PRIMARY KEY (chat_id)
- **chats_user_id_fkey**: FOREIGN KEY (user_id) REFERENCES users(user_id)
- **unique_chat_id**: UNIQUE (chat_id)

---

## chunks

### Columns

| Column Name | Data Type | Nullable | Default | Max Length |
|------------|-----------|----------|----------|------------|
| chunk_id | text | NO | None | N/A |
| document_id | text | YES | None | N/A |
| folder_id | text | YES | None | N/A |
| user_id | text | YES | None | N/A |
| content | text | YES | None | N/A |
| embedding | bytea | YES | None | N/A |
| content_tsvector | tsvector | YES | None | N/A |

### Constraints

- **chunks_pkey**: PRIMARY KEY (chunk_id)

---

## clients

### Columns

| Column Name | Data Type | Nullable | Default | Max Length |
|------------|-----------|----------|----------|------------|
| client_id | uuid | NO | gen_random_uuid() | N/A |
| user_id | character varying | YES | None | 255 |
| name | text | NO | None | N/A |

### Constraints

- **clients_pkey**: PRIMARY KEY (client_id)
- **clients_user_id_fkey**: FOREIGN KEY (user_id) REFERENCES users(user_id)

---

## files

### Columns

| Column Name | Data Type | Nullable | Default | Max Length |
|------------|-----------|----------|----------|------------|
| file_id | uuid | NO | gen_random_uuid() | N/A |
| client_id | uuid | YES | None | N/A |
| lockr_id | uuid | YES | None | N/A |
| user_id | character varying | YES | None | 255 |
| chat_id | character varying | YES | None | 255 |
| file_name | text | YES | None | N/A |
| file_type | text | YES | None | N/A |
| file_last_modified | timestamp without time zone | YES | None | N/A |
| file_link | text | YES | None | N/A |
| file_location | text | YES | None | N/A |
| file_size | bigint | YES | None | N/A |

### Constraints

- **files_chat_id_fkey**: FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
- **files_client_id_fkey**: FOREIGN KEY (client_id) REFERENCES clients(client_id)
- **files_lockr_id_fkey**: FOREIGN KEY (lockr_id) REFERENCES lockr(lockr_id)
- **files_pkey**: PRIMARY KEY (file_id)
- **files_user_id_fkey**: FOREIGN KEY (user_id) REFERENCES users(user_id)

---

## lockr

### Columns

| Column Name | Data Type | Nullable | Default | Max Length |
|------------|-----------|----------|----------|------------|
| lockr_id | uuid | NO | gen_random_uuid() | N/A |
| user_id | character varying | YES | None | 255 |
| name | text | NO | None | N/A |

### Constraints

- **lockr_pkey**: PRIMARY KEY (lockr_id)
- **lockr_user_id_fkey**: FOREIGN KEY (user_id) REFERENCES users(user_id)

---

## users

### Columns

| Column Name | Data Type | Nullable | Default | Max Length |
|------------|-----------|----------|----------|------------|
| user_id | character varying | NO | gen_random_uuid() | 255 |
| username | text | NO | None | N/A |

### Constraints

- **users_pkey**: PRIMARY KEY (user_id)

---

