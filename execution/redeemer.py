import logging
from web3 import Web3
from config import POLYMARKET_PRIVATE_KEY, CHAINLINK_RPC_URL

logger = logging.getLogger(__name__)

# Polymarket CTF (Conditional Tokens Framework) contract address on Polygon
CTF_ADDRESS = "0x4b789C133744637D066b57116752077e6833D72E"
CTF_ABI = [
    {
        "name": "redeem",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSet", "type": "uint256[]"}
        ],
        "outputs": []
    }
]

class Redeemer:
    def __init__(self, rpc_url: str = None):
        self.rpc_url = rpc_url or CHAINLINK_RPC_URL
        if not self.rpc_url:
            logger.error("No RPC URL provided for Redeemer")
            self.w3 = None
            return
            
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not POLYMARKET_PRIVATE_KEY:
            logger.error("POLYMARKET_PRIVATE_KEY not set — cannot redeem")
            self.w3 = None
            return
            
        self.account = self.w3.eth.account.from_key(POLYMARKET_PRIVATE_KEY)
        self.contract = self.w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)

    def redeem(self, condition_id: str, index_set: list) -> bool:
        """
        Redeems a specific condition and index set.
        index_set: [1] for YES, [2] for NO in typical binary markets.
        """
        if not self.w3: return False
        try:
            # Polymarket condition_id is usually a hex string
            c_bytes = Web3.to_bytes(hexstr=condition_id)
            
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            
            # Simple gas estimation or fixed for Polygon
            tx = self.contract.functions.redeem(
                c_bytes,
                index_set
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': 150000,
                'gasPrice': self.w3.eth.gas_price
            })
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, POLYMARKET_PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logger.info("Redemption TX sent | condition=%s... | hash=%s", 
                        condition_id[:10], tx_hash.hex())
            
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status == 1:
                logger.info("Redemption SUCCESS | condition=%s", condition_id[:10])
                return True
            else:
                logger.error("Redemption FAILED | receipt status=0")
                return False
                
        except Exception as e:
            logger.error("Redemption error: %s", e)
            return False

    def redeem_all(self, trades: list):
        """
        Redeems multiple trades. 
        trades: list of dicts with 'condition_id' and 'index_set'.
        """
        if not trades: return
        logger.info("Starting batch redemption for %d trades", len(trades))
        for trade in trades:
            c_id = trade.get("condition_id")
            i_set = trade.get("index_set", [1, 2]) # Usually both are checked for safety or specific win
            if c_id:
                self.redeem(c_id, i_set)
