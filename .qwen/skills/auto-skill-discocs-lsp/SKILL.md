---
name: discocs-lsp
description: Understanding LSP functionality in the Discocs project, code navigation and analysis capabilities
source: auto-skill
extracted_at: '2026-06-25T19:12:32.699Z'
---

# Discocs LSP Functionality and Code Navigation

## Overview of Language Server Protocol Support

The Discocs project benefits from Language Server Protocol (LSP) functionality for enhanced code navigation, analysis, and development assistance. LSP capabilities provide rich IDE-like features to editors when working with the Discocs codebase.

## Available LSP Operations

The LSP in the Discocs project supports these main operations:

### Document Symbol Exploration
Use `documentSymbol` operation to retrieve:
- Classes and methods in files like `app/main.py`, `app/store.py`, `app/recommender.py`
- Variable declarations with their position and type
- Hierarchical symbols including nested elements in dataclasses
- Constants, functions, and class methods with location info

Example:
```python
# Fetch all symbols in a file
lsp(documentSymbol, filePath="app/store.py")
```

### Definition Navigation
Use `goToDefinition` operation to jump to:
- Class/variable/function declaration locations 
- Method implementations
- Import targets

### Hover Information
Use `hover` operation to retrieve:
- Type information for variables and parameters
- Docstrings and documentation
- Module import details
- Parameter descriptions

### Workspace Analysis
Use `workspaceSymbol` operation to locate:
- Symbols across the entire project
- Classes, functions, constants by name

### Diagnostics
Use `diagnostics` and `workspaceDiagnostics` to:
- Check for project-wide issues
- Analyze specific files for problems
- Validate code compliance

## LSP Usage Patterns in Discocs

### Exploring the FastAPI Application Structure
The main application in `app/main.py` contains hundreds of symbols representing:
- API route handlers (endpoints)
- Pydantic request/response models
- Configuration constants
- Global application state management

Using LSP helps navigate this complex file effectively.

### Analyzing the Database Schema
The Store class (`app/store.py`) defines a comprehensive data layer with:
- Data models and relationships
- SQL operations with transaction management
- Index definitions and table structures

LSP helps explore these database interaction methods and models.

### Understanding Embedding and Prediction Pipelines
The `app/embedder.py`, `app/recommender.py`, and model analysis code benefit from:
- Quick jumping to embedding computation methods
- Inspection of vector operations
- Understanding model input/output flows

## Typical LSP Workflows in Discocs Development

1. **Discover code organization**: Use `documentSymbol` to understand structure of large files
2. **Navigate related code**: Use `goToDefinition` to trace function implementations
3. **Verify type information**: Use `hover` to check variable types and signatures
4. **Locate entities globally**: Use `workspaceSymbol` to find functions/classes across the project
5. **Identify issues**: Use `diagnostics` to detect potential problems

## Integration Points

LSP integrates well with the existing development workflows in Discocs:
- Works seamlessly with pydantic models used throughout the codebase
- Understands fastAPI routing and dependency injection
- Follows the repository pattern established in the Store class
- Works with the configuration system via pydantic settings

## Best Practices for LSP in Discocs Development

- Use LSP for navigating the substantial `main.py` file to quickly find API endpoints
- Utilize symbol search when refactoring data models in the store layer
- Check diagnostics before running tests to catch issues early
- Use hover features to understand complex methods in recommendations engine
- Take advantage of definition jumping when tracing how CLI commands connect to store operations