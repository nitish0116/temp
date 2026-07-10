# Python `asyncio` Library Explained

`asyncio` is Python's built-in library for writing **asynchronous, concurrent code** using `async`/`await` syntax. It lets you run multiple I/O-bound operations concurrently without threading.

## Key Concepts

### 1. **Coroutines** (async functions)
Functions defined with `async def` that can be paused and resumed.

```python
async def fetch_data():
    # This function can await other async functions
    await some_operation()
    return result
```

### 2. **await** keyword
Pauses execution until an async operation completes, allowing other code to run.

```python
async def main():
    result = await fetch_data()  # Waits for fetch_data to finish
    print(result)
```

### 3. **Event Loop**
The core engine that manages and schedules coroutines.

```python
asyncio.run(main())  # Creates event loop, runs main(), cleans up
```

### 4. **Tasks**
Wrappers around coroutines that schedule them for concurrent execution.

```python
task1 = asyncio.create_task(fetch_data())
task2 = asyncio.create_task(fetch_data())
```

---

## How It Works in Your Code

Your `md_to_audio.py` uses asyncio for **parallel Edge TTS chunk synthesis**:

### Semaphore (Rate Limiting)
```python
semaphore = asyncio.Semaphore(workers=8)

async def synthesize_one(index: int, chunk_text: str):
    async with semaphore:
        # Max 8 concurrent requests
        await edge_tts.Communicate(text=chunk_text, voice=voice_name).save(path)
```

**Why:** Edge API likely throttles requests. Semaphore limits concurrent requests to 8 instead of trying to synthesize all 2000+ chunks simultaneously.

### Creating Parallel Tasks
```python
tasks = [
    asyncio.create_task(synthesize_one(i, chunk))
    for i, chunk in enumerate(chunks)
]
```

Schedules all synthesis tasks concurrently.

### Processing Results as They Complete
```python
for task in asyncio.as_completed(tasks):
    index, chunk_path = await task
    print(f"Synthesized chunk {index}")
```

Processes results in completion order, not execution order.

---

## Practical Examples

### Example 1: Simple Async Function

```python
import asyncio
import time

async def greet(name, delay):
    await asyncio.sleep(delay)  # Simulate I/O operation
    print(f"Hello, {name}!")

async def main():
    # Run sequentially (takes 3 seconds total)
    await greet("Alice", 1)
    await greet("Bob", 1)
    await greet("Charlie", 1)

asyncio.run(main())
# Output (after 3 seconds):
# Hello, Alice!
# Hello, Bob!
# Hello, Charlie!
```

### Example 2: Concurrent Tasks (Parallel)

```python
async def main():
    # Run concurrently (takes ~1 second total)
    await asyncio.gather(
        greet("Alice", 1),
        greet("Bob", 1),
        greet("Charlie", 1)
    )

asyncio.run(main())
# Output (after ~1 second):
# Hello, Alice!
# Hello, Bob!
# Hello, Charlie!
```

### Example 3: Limiting Concurrency with Semaphore

```python
async def download_file(url, semaphore):
    async with semaphore:
        print(f"Downloading {url}...")
        await asyncio.sleep(2)  # Simulate download
        print(f"Downloaded {url}")

async def main():
    semaphore = asyncio.Semaphore(2)  # Max 2 concurrent downloads
    
    urls = ["file1.zip", "file2.zip", "file3.zip", "file4.zip"]
    tasks = [download_file(url, semaphore) for url in urls]
    await asyncio.gather(*tasks)

asyncio.run(main())
# Output:
# Downloading file1.zip...
# Downloading file2.zip...
# Downloaded file1.zip
# Downloading file3.zip...
# Downloaded file2.zip
# Downloading file4.zip...
# Downloaded file3.zip
# Downloaded file4.zip
```

### Example 4: Processing Results as They Complete

```python
async def fetch(item, delay):
    await asyncio.sleep(delay)
    return f"Result: {item}"

async def main():
    tasks = [
        asyncio.create_task(fetch("A", 3)),
        asyncio.create_task(fetch("B", 1)),
        asyncio.create_task(fetch("C", 2))
    ]
    
    # Results in completion order (B, C, A)
    for task in asyncio.as_completed(tasks):
        result = await task
        print(result)

asyncio.run(main())
# Output (in completion order):
# Result: B      (completes after 1 sec)
# Result: C      (completes after 2 secs)
# Result: A      (completes after 3 secs)
```

---

## Why Your Code Uses It

**Without asyncio (sequential):** 2000 chunks × 2 sec per chunk = ~1 hour
```python
for chunk in chunks:
    synthesize(chunk)  # Waits 2 sec each
```

**With asyncio (parallel, 8 workers):** ~15 minutes
```python
semaphore = asyncio.Semaphore(8)
tasks = [synthesize_with_limit(chunk) for chunk in chunks]
await asyncio.gather(*tasks)  # All run concurrently
```

---

## Key Takeaway

`asyncio` shines for **I/O-bound operations** (network calls, file reads, API requests). It's not for CPU-bound work (use `multiprocessing` or `threading` instead).

Your Edge TTS synthesis is **I/O-bound** (waiting for network responses), so asyncio is perfect.