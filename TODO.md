# Task 1: Shared Math Utility
- [x] Step 1: Write initial tests for hedging and spread
- [x] Step 2: Implement VWAP and Hedge Price
- [x] Step 3: Run tests to verify logic
- [x] Step 4: Commit

# Task 2: Generalized PolymarketFeed
- [x] Step 1: Replace fixed market properties in `PolymarketFeed.__init__`
- [x] Step 2: Add Compatibility Layer
- [x] Step 3: Update `fetch_market` to `fetch_markets_by_pattern`
- [x] Step 4: Maintain L2 Orderbook Depth
- [x] Step 5: Verify multi-market subscription and compatibility
- [x] Step 6: Commit

# Task 3: Dynamic Orchestration
- [x] Step 1: Add Bot Enable Flags to `config.py`
- [x] Step 2: Update `Orchestrator.run` for dynamic task creation
- [x] Step 3: Update shutdown logic for all bots
- [x] Step 4: Commit

# Task 4: Risk & Redemption Foundation
- [x] Step 1: Outline `Redeemer` class with `web3.py`
- [x] Step 2: Implement Global Risk Health Check
- [x] Step 3: Integrate `Redeemer` into `Orchestrator` shutdown
- [x] Step 4: Commit

# Task 5: Bot C Implementation (GLOB Arb)
- [x] Step 1: Implement `signals/signal_c.py` (VWAP Arb logic)
- [x] Step 2: Implement `bots/bot_c.py` (Multi-market monitor)
- [x] Step 3: Register `BotC` in `Orchestrator`
- [x] Step 4: Verify discovery and paper mode

# Task 6: Bot E (Momentum) Implementation
- [x] Step 1: Implement `signals/signal_e.py` (Velocity pulse logic)
- [x] Step 2: Generalize `ExecutionLayer` for multi-market tokens
- [x] Step 3: Implement `bots/bot_e.py` (Price trending)
- [x] Step 4: Register `BotE` and verify

# Task 7: Bot F (Copytrade) Implementation
- [x] Step 1: Implement `signals/signal_f.py`
- [x] Step 2: Implement `bots/bot_f.py`
- [x] Step 3: Add `get_slug_accuracies` to `db.py`

# Task 8: Bot G (Crypto) Implementation
- [x] Step 1: Implement `signals/signal_g.py`
- [x] Step 2: Implement `bots/bot_g.py`
