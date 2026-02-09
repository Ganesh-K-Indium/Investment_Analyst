"""
Benchmark utilities for tracking node execution times in the workflow graph.
"""
import time
from typing import Dict, Any
from functools import wraps

class NodeTimer:
    """Simple utility to track and print node execution times."""
    
    def __init__(self):
        self.start_times = {}
        self.execution_times = {}
        self.total_start_time = None
        
    def start_total_timer(self):
        """Start timing the entire workflow."""
        self.total_start_time = time.time()
        print(f" Starting workflow execution at {time.strftime('%H:%M:%S')}")
        
    def start_node_timer(self, node_name: str):
        """Start timing a specific node."""
        self.start_times[node_name] = time.time()
        print(f"  Entering node: {node_name} at {time.strftime('%H:%M:%S')}")
        
    def end_node_timer(self, node_name: str):
        """End timing a specific node and print the duration."""
        if node_name in self.start_times:
            duration = time.time() - self.start_times[node_name]
            self.execution_times[node_name] = duration
            print(f" Completed node: {node_name} - Duration: {duration:.2f} seconds")
            return duration
        return 0
        
    def print_summary(self):
        """Print a summary of all node execution times."""
        if self.total_start_time:
            total_duration = time.time() - self.total_start_time
            print(f"\n WORKFLOW EXECUTION SUMMARY")
            print(f"=" * 50)
            print(f"Total workflow time: {total_duration:.2f} seconds")
            print(f"Individual node times:")
            
            for node_name, duration in self.execution_times.items():
                percentage = (duration / total_duration) * 100
                print(f"  • {node_name}: {duration:.2f}s ({percentage:.1f}%)")
            
            print(f"=" * 50)

# Global timer instance
node_timer = NodeTimer()

def time_node(node_name: str):
    """Decorator to time node execution."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            node_timer.start_node_timer(node_name)
            try:
                result = func(*args, **kwargs)
                node_timer.end_node_timer(node_name)
                return result
            except Exception as e:
                print(f"❌ Error in node {node_name}: {str(e)}")
                node_timer.end_node_timer(node_name)
                raise
        return wrapper
    return decorator