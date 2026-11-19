

## models.py

Instead of SequenceSpec, define Pipeline and Step.

## steps.py

1. Start with a Step object
```python
from poethepoet.app import PoeThePoet,PoeConfig

# Code to convert steps --> sequence and serialized to dict and then created PoeConfig.
app = PoeThePoet(config=xxxx)

```
