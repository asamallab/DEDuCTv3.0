# DEDuCT-KG files

This folder contains the node and edge files for DEDuCT-KG, along with a script to load them into a Neo4j database.

## Contents

- [node_tables](./node_tables/) - Data on the nodes present in DEDuCT-KG. The nodes are split into 7 node types:
    - [AOP.nodes.tsv](./node_tables/AOP.nodes.tsv) - 385 Adverse Outcome Pathway nodes curated from [AOP-Wiki](https://aopwiki.org/). For each node, this file provides the unique identifier, name, OECD status associated with the AOP, and the node type.
    - [Chemical.nodes.tsv](./node_tables/Chemical.nodes.tsv) - 1043 chemical nodes present in DEDuCTv3.0. For each chemical, this file provides the unique identifier, chemical name, Chemical Abstracts Service Registry Number (CASRN), PubChem chemical identifier (CID), and the node type.
    - [DEDuCT_Endpoint.nodes.tsv](./node_tables/DEDuCT_Endpoint.nodes.tsv) - 796 endocrine-mediated endpoint nodes present in DEDuCTv3.0. For each endpoint, this file provides the unique identifier, name, systems-level perturbation information, and the node type.
    - [Disease.nodes.tsv](./node_tables/Disease.nodes.tsv) - 3098 disease nodes curated in DEDuCT-KG. For each disease, this file provides the unique identifier, name, and the node type.
    - [Gene.nodes.tsv](./node_tables/Gene.nodes.tsv) - 48999 gene nodes curated in DEDuCTv3.0. For each gene, this file provides the unique identifier, the gene symbol as the name of the node, and the node type.
    - [KeyEvent.nodes.tsv](./node_tables/KeyEvent.nodes.tsv) - 1547 key event nodes curated from [AOP-Wiki](https://aopwiki.org/). For each key event, this file provides the unique identifier, the key event name, level of biological organization (BOL) associated with the key event, and the node type.
    - [Phenotype.nodes.tsv](./node_tables/Phenotype.nodes.tsv) - 19238 phenotype nodes curated in DEDuCTv3.0. For each phenotype, this file provides the unique identifier, name of the phenotype, and the node type.

- [edge_tables](./edge_tables/) - Data on the edges present in DEDuCT-KG. Files are named `{node_type_1}.{edge_type}.{node_type_2}.edges.tsv`, where `node_type_1` and `node_type_2` are the types of the nodes the edge connects, and `edge_type` is the type of the edge. For example, [Chemical.affects_expression_of.Gene.edges.tsv](./edge_tables/Chemical.affects_expression_of.Gene.edges.tsv) contains edges from `Chemical` nodes to `Gene` nodes of type `affects_expression_of`. Each file contains the source node identifier, target node identifier, and metadata such as the reference for the edge and the source from which the reference was curated (if any). This folder contains 35 files, spanning 31 different edge types connecting the 7 node types.

- [ingest_tsv_to_neo4j.py](./ingest_tsv_to_neo4j.py) - Python script that loads the node and edge tables into a Neo4j database (see below).

## Loading DEDuCT-KG into Neo4j

### Requirements

- Python 3.8+
- A running Neo4j instance (tested with Neo4j Community Edition 2025.11.2)
- Python packages:

```bash
pip install neo4j pandas numpy
```

The [APOC plugin](https://neo4j.com/labs/apoc/) is optional. It is only used to wipe the schema faster; the script falls back to dropping constraints and indexes manually.

### Configuration

Open `ingest_tsv_to_neo4j.py` and fill in the connection details near the top of the file:

```python
NEO4J_URL = 'bolt://localhost:7687'   # Neo4j Bolt address
NEO4J_USER = 'neo4j'
NEO4J_PASSWORD = 'your_password'
```

The script loads into the default database named `neo4j`.

### Usage

Run the script from this folder, passing the node and edge directories:

```bash
python ingest_tsv_to_neo4j.py node_tables edge_tables
```

> **Warning:** the script **erases the target database** (all nodes, relationships, constraints and indexes) before loading, and does not ask for confirmation.

### What the script does

1. Wipes the target database (data and schema).
2. For every `*.nodes.tsv` file, creates a uniqueness constraint and index on `id`, then loads the nodes. The node label is taken from the file name, and duplicate ids are merged (last row wins).
3. For every `*.edges.tsv` file, matches the source and target nodes by `id` and creates the relationship. The relationship type is taken from the file name, and every column other than `source` and `target` is stored as a relationship property.
4. Copies each node's `name` to a `label` property, which the DEDuCT-KG web interface uses as node text.

Each directory you pass is scanned for both `*.nodes.tsv` and `*.edges.tsv` files, and within a directory all nodes are loaded before any edges. You can therefore either:

- pass the node and edge folders separately, **node folder first** (`python ingest_tsv_to_neo4j.py node_tables edge_tables`), or
- put all files in a single folder and pass just that one.

If the edge folder is passed before the node folder, the edges will silently not be created, because their source and target nodes do not exist yet.

### Input file conventions

| File type | Name pattern | Required columns |
|---|---|---|
| Nodes | `<NodeType>.nodes.tsv` | `id` |
| Edges | `<SourceType>.<edge_type>.<TargetType>.edges.tsv` | `source`, `target` |

Files must be tab-separated. Empty cells in node files are skipped, so no property is stored for them.

### Verifying the load

In Neo4j Browser or `cypher-shell`:

```cypher
MATCH (n) RETURN labels(n)[0] AS node_type, count(*) AS total ORDER BY total DESC;
MATCH ()-[r]->() RETURN type(r) AS edge_type, count(*) AS total ORDER BY total DESC;
```

The node counts should match those listed above.