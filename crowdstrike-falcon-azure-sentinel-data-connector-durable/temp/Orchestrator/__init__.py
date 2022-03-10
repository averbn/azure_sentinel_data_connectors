import azure.functions as func
import azure.durable_functions as df

# def orchestrator_function(context: df.DurableOrchestrationContext):
#     # Get a list of N work items to process in parallel.
#     data1 = context._input
#     # parallel_tasks = [ context.call_activity("FilesWorker", b) for b in data ]
#     # yield context.task_all(parallel_tasks)
# main = df.Orchestrator.create(orchestrator_function)