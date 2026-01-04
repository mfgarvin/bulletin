# Bulletin Parser

Extracts Catholic parish information from church bulletins using GPT-4o.

**Extracts:** Mass times, confession times, adoration schedules, parish contact info, and events (retreats, fish fries, bible studies, etc.)

## Setup

1. Python 3.12+
2. `pip install -r requirements.txt`
3. Copy `.env.template` to `.env` and fill in:
   - `OPENAI_API_KEY` - from [OpenAI](https://platform.openai.com/api-keys)
   - `NOTION_API_KEY` - from [Notion](https://www.notion.so/my-integrations)
   - `PARISH_DB_ID` - your Notion database ID

## Usage

```bash
# Load environment
source .env

# Process all enabled parishes with stale data (>7 days old)
python main.py --all

# Process specific parish
python main.py YOUR_PARISH_ID

# Dry run (extract but don't save)
python main.py --dry-run --all

# Verbose output
python main.py -v --all
```

## Bulletin Sources

| Publisher | ID Format | Example |
|-----------|-----------|---------|
| Parishes Online | numeric | `0020` |
| Discover Mass | slug | `st-james-the-less-columbus-oh` |
| eCatholic | numeric | `1234` |

## Automation

GitHub Actions runs weekly on Saturdays at 2 PM UTC.

---

### Diocese Coverage

| Diocese | Parishes |
|---------|----------|
| Toledo | 134 |
| Steubenville | 54 |
| Cincinnati | 219 |
| Youngstown | 115 |
| Cleveland | 187 |
| Columbus | 111 |
| **Total** | **820** |
