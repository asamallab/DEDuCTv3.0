# ingest_tsv_to_neo4j.py
#
# Loads DEDuCT-KG node and edge TSV files into a Neo4j database.
#
# Expected input files (in the directories passed on the command line):
#   <NodeType>.nodes.tsv                               -> one node label per file; needs an 'id' column
#   <SourceType>.<relation>.<TargetType>.edges.tsv     -> one relationship type per file; needs 'source' and 'target' columns
#
# WARNING: the target database is completely wiped (data, constraints, indexes) before loading.
#
# Usage: python <this_script>.py node_tables edge_tables

import os
import sys
import pandas as pd
from glob import glob
from neo4j import GraphDatabase
#from dotenv import load_dotenv  # optional: load credentials from a .env file instead of editing them below
import numpy as np
import re

# ==========================================
# 1. CONFIGURATION SECTION
# ==========================================

# Fill in your connection details below. Do NOT commit real credentials to GitHub.
NEO4J_URL = '<neo4j-url>' # <-- Neo4j local port, e.g. bolt://localhost:7687
NEO4J_USER = '<db-username>'  # <-- fill this data
NEO4J_PASSWORD = '<db-password>' # <-- fill this in (or load it from a .env file)

print(f"Connecting to Neo4j at {NEO4J_URL} as {NEO4J_USER}")

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def clear_database():
    """Wipes the database (Data + Schema) before starting ingestion."""
    print(" Wiping existing database (Data & Schema)...")
    try:
        with GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
            with driver.session(database="neo4j") as session:
                # 1. Delete all Data (Nodes & Relationships)
                session.run("MATCH (n) DETACH DELETE n")
                
                # 2. Delete Schema (Constraints & Indexes)
                # Attempt to use APOC for a one-line wipe (fastest)
                try:
                    session.run("CALL apoc.schema.assert({},{})")
                    print("   -> Schema wiped using APOC.")
                except Exception:
                    # Fallback: Manually drop constraints and indexes if APOC is missing
                    print("   -> APOC not found, dropping schema manually...")
                    constraints = session.run("SHOW CONSTRAINTS")
                    for record in constraints:
                        session.run(f"DROP CONSTRAINT {record['name']}")
                    
                    indexes = session.run("SHOW INDEXES")
                    for record in indexes:
                        session.run(f"DROP INDEX {record['name']}")
                        
        print(" Database completely cleared.")
    except Exception as e:
        print(f" Error clearing database: {e}")
        sys.exit(1)

def df_parser_node(df):
    for i in df.iterrows():
        props = i[1].dropna().to_dict()
        props["id"] = str(props["id"])
        yield props

def df_parser_edge(df):
    for i in df.iterrows():
        props = i[1].dropna().to_dict()
        source = str(props.pop('source'))
        target = str(props.pop('target'))
        yield (source, props, target)

# Create a uniqueness constraint and an index on 'id' for one node type.
# node_type: label as used in Cypher (backtick-quoted if it contains spaces)
# name: space-free version of the label, used in the constraint/index names
def index_nodes(node_type, name):
    with GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
        with driver.session(database="neo4j") as session:
            tx = session.begin_transaction()
            try:
                # Using standard Cypher syntax for constraints/indexes
                tx.run(f"CREATE CONSTRAINT unique_id_{name} IF NOT EXISTS FOR (n:{node_type}) REQUIRE n.id IS UNIQUE")
                tx.run(f"CREATE INDEX index_id_{name} IF NOT EXISTS FOR (n:{node_type}) ON (n.id)")
                # tx.run(f"CREATE INDEX index_label_{name} IF NOT EXISTS FOR (n:{node_type}) ON (n.label)")
                tx.commit()
            except Exception as e:
                print(f" Index error (safe to ignore if exists): {e}")
                tx.rollback()
            finally:
                tx.close()

# Create nodes of one type in batches of `limit` (one transaction per batch).
# Returns True if all batches succeeded; stops at the first failed batch.
def ingest_node(node_type, nodes, limit=5000):
    success = True
    with GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
        with driver.session(database="neo4j") as session:
            skip = 0
            while skip < len(nodes):
                batch = nodes[skip: skip+limit]
                tx = session.begin_transaction()
                try:
                    # Using MERGE is safer for idempotency, but CREATE is faster for empty DB
                    # Since we are wiping the DB, CREATE is fine.
                    query = f'''
                        UNWIND $batch as map
                        CREATE (n:{node_type})
                        SET n = map
                    '''
                    tx.run(query, {"batch": batch})
                    skip += limit
                    tx.commit()
                except Exception as e:
                    print(" Error ingesting nodes, rolling back...")
                    print(" Exception", e)
                    tx.rollback()
                    success = False
                    break
                finally:
                    tx.close()
            else:
                success = True            
    return success

# Create relationships of one type in batches of `limit` (one transaction per batch).
# Source and target nodes are matched on 'id', so nodes must be ingested first.
# meta: property map body built from the file's extra columns, e.g. "ref: row.ref"
# Returns True if all batches succeeded; stops at the first failed batch.
def ingest_edges(relation, meta, source, target, edges, limit=5000):
    success = True
    with GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
        with driver.session(database="neo4j") as session:
            skip = 0
            while skip < len(edges):
                batch = edges[skip: skip+limit]
                tx = session.begin_transaction()
                try:
                    # Uses CREATE (not MERGE): safe here because the database is wiped first.
                    # Re-running without the wipe would create duplicate edges.
                    query = f'''
                        UNWIND $batch as row
                        MATCH (n:{source}), (m:{target})
                        WHERE n.id=row.source and m.id=row.target
                        CREATE (n)-[r:{relation} {{
                            {meta}
                        }}]->(m)
                    '''
                    tx.run(query, {"batch": batch})
                    skip += limit
                    tx.commit()
                except Exception as e:
                    print(" Error ingesting edges, rolling back...")
                    print(" Exception", e)
                    tx.rollback()
                    success = False
                    break
                finally:
                    tx.close()
            else:
                success = True            
    return success

# --- UI FIX FUNCTIONS ---
# The graph web interface displays the 'label' property as node/edge text.
def add_ui_labels():
    """Maps 'name' to 'label' so the Graph UI shows text on nodes."""
    print("\n Applying UI fixes (Mapping 'name' -> 'label')...")
    query = "MATCH (n) WHERE n.name IS NOT NULL SET n.label = n.name RETURN count(n) as updated"
    
    with GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
        with driver.session(database="neo4j") as session:
            result = session.run(query)
            count = result.single()["updated"]
            print(f"    Updated {count} nodes for UI display.")

def add_edge_labels():
    """Maps 'relation' to 'label' so the Graph UI shows text on edges (Optional)."""
    print(" Applying Edge fixes (Mapping 'relation' -> 'label')...")
    query = "MATCH ()-[r]->() WHERE type(r) IS NOT NULL SET r.label = type(r) RETURN count(r) as updated"
    
    with GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
        with driver.session(database="neo4j") as session:
            result = session.run(query)
            count = result.single()["updated"]
            print(f"    Updated {count} edges for UI display.")

# ==========================================
# 3. MAIN EXECUTION
# ==========================================

if __name__ == "__main__":
    directories = sys.argv[1:]
    if not directories:
        print(" Please provide the directory path as an argument. Example: python ingest_csv.py data_import")
        sys.exit(1)

    # 1. Clean the database first (Data + Metadata)
    clear_database()

    # 2. File-name patterns (the label / relation / types are read from the file names)
    #    nodes: <dir>/<label>.<entity>.tsv
    #    edges: <dir>/<source_type>.<relation>.<target_type>.<entity>.tsv
    node_pattern = "(?P<directory>.+)/(?P<label>.+)\.(?P<entity>.+)\.tsv"
    edge_pattern = "(?P<directory>.+)/(?P<source_type>.+)\.(?P<relation>.+)\.(?P<target_type>.+)\.(?P<entity>.+)\.tsv"

    for directory in directories:
        directory = directory.strip()
        print(f" Scanning directory: {directory}")

        # --- NODES ---
        found_nodes = glob(directory + "/*.nodes.tsv")
        print(f"    Found {len(found_nodes)} node files.")
        for filename in found_nodes:
            match = re.match(node_pattern, filename).groupdict()
            entity = match["entity"]
            label = match["label"]
            
            # Handle spaces in labels
            n = f"`{label}`" if " " in label else label
            safe_name = label.replace(" ", "_")
            
            # Create uniqueness constraint + index on id for this node type
            index_nodes(n, safe_name) 
            print(f"   Processing Nodes: {label}...")
            
            node_dict = {}
            
            # READ AS TSV
            try:
                df = pd.read_csv(filename, sep='\t')
                
                # Convert ID to string to ensure matching works
                if 'id' in df.columns:
                    df['id'] = df['id'].astype(str).str.strip()
                
                for k,row in df.iterrows():
                    v = {}
                    for i,j in row.items():
                        if isinstance(j, str):
                            v[i] = j
                        elif not pd.isna(j):
                            v[i] = int(j) if isinstance(j, (int, np.integer)) else float(j)
                    
                    # Deduplication logic (last write wins)
                    node_id = v.get("id", k)
                    
                    if node_id not in node_dict:
                        node_dict[node_id] = {**v}
                    else:
                        node_dict[node_id].update(v)
                
                ingest_node(n, list(node_dict.values()))
            except Exception as e:
                print(f" Error reading {filename}: {e}")

        # --- EDGES ---
        found_edges = glob(directory + "/*.edges.tsv")
        print(f"    Found {len(found_edges)} edge files.")
        for filename in found_edges:
            match = re.match(edge_pattern, filename).groupdict()
            source_type = f"`{match['source_type']}`"
            relation = f"`{match['relation']}`"
            target_type = f"`{match['target_type']}`"
        
            print(f"   Processing Edges: {match['source_type']} -> {match['relation']} -> {match['target_type']}")
            
            try:
                # READ AS TSV
                df = pd.read_csv(filename, sep='\t')
                
                # Ensure source/target are strings
                df['source'] = df['source'].astype(str).str.strip()
                df['target'] = df['target'].astype(str).str.strip()
                
                # Every column except source/target becomes a relationship property
                meta = []
                for col in df.columns:
                    if (col not in ["source", 'target']):
                        meta.append(f"{col}: row.{col}")
                
                # One dict per row, passed to Cypher as the $batch parameter
                edges = list(df.to_dict(orient="index").values())
                print(f"     -> Ingesting {len(edges)} relations")
                
                meta_str = ",\n".join(meta)
                success = ingest_edges(relation, meta_str, source_type, target_type, edges)
                if not success:
                    print(f" Warning: Some edges in {filename} failed to ingest.")
            except Exception as e:
                print(f" Error reading {filename}: {e}")

    # 3. Apply UI fixes: copy 'name' to 'label' so nodes show text in the graph UI
    add_ui_labels()
    # add_edge_labels() # Uncomment this if you want edge text in the UI too

    print("\n Ingestion Complete!")