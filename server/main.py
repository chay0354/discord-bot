"""Legacy entry — prefer: python run.py"""
from run import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
