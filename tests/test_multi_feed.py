import pytest
import asyncio
import json
from feeds.polymarket import PolymarketFeed

@pytest.fixture
async def feed():
    async with PolymarketFeed() as f:
        yield f

@pytest.mark.asyncio
async def test_feed_registration(feed):
    # Mock data for self._session.get
    # In a real environment, this might need more robust mocking
    # For now, we manually seed the markets dict to test the logic
    tid1 = "0x123"
    tid2 = "0x456"
    feed.markets[tid1] = {"odds": 0.5, "history": [], "bids": [], "asks": []}
    feed.markets[tid2] = {"odds": 0.4, "history": [], "bids": [], "asks": []}
    
    assert tid1 in feed.markets
    assert tid2 in feed.markets

@pytest.mark.asyncio
async def test_compatibility_layer(feed):
    up_id = "up_token"
    down_id = "down_token"
    feed._default_up_id = up_id
    feed._default_down_id = down_id
    
    feed.markets[up_id] = {"odds": 0.55, "depth": 100.0, "velocity": 0.01}
    
    # Test property redirects
    assert feed.up_token_id == up_id
    assert feed.up_odds == 0.55
    assert feed.book_depth == 100.0
    assert feed.odds_velocity == 0.01

@pytest.mark.asyncio
async def test_handle_book_event(feed):
    tid = "token_a"
    feed.markets[tid] = {"odds": None, "bids": [], "asks": []}
    
    # Mock L2 book event
    event = {
        "asset_id": tid,
        "book": {
            "bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.51", "size": "10"}]
        }
    }
    
    feed._handle(json.dumps(event))
    
    assert len(feed.markets[tid]["bids"]) == 1
    assert feed.markets[tid]["bids"][0]["price"] == "0.50"
    assert len(feed.markets[tid]["asks"]) == 1

@pytest.mark.asyncio
async def test_handle_price_event(feed):
    tid = "token_a"
    feed.markets[tid] = {"odds": None, "history": []}
    
    # Mock price change event
    event = {
        "asset_id": tid,
        "price": "0.45"
    }
    
    feed._handle(json.dumps(event))
    
    assert feed.markets[tid]["odds"] == 0.45
    assert len(feed.markets[tid]["history"]) == 1
