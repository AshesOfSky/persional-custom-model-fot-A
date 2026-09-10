from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'packages'))
if __name__ == '__main__':
    import argparse
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
    import uvicorn
    parser = argparse.ArgumentParser(description='Market Strategy Terminal API')
    parser.add_argument('--port', type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run('apps.api.composition:create_production_app', factory=True, host='127.0.0.1', port=args.port)
