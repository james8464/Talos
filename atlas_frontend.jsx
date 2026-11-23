import React, { useEffect, useState, useRef } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import axios from 'axios';

const TalosAtlas = () => {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [focusNode, setFocusNode] = useState(null);
  const fgRef = useRef();

  useEffect(() => {
    // Initial data fetch
    const fetchData = async () => {
      try {
        const res = await axios.get('http://localhost:8000/graph');
        setGraphData(res.data);
      } catch (e) {
        console.error("Atlas Backend unavailable", e);
      }
    };
    fetchData();

    // WebSocket for real-time TUI sync
    const ws = new WebSocket('ws://localhost:8000/ws');
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === 'node_focused') {
        setFocusNode(msg.node_id);
        // Fly the camera to the focused node
        fgRef.current.zoomToNode(msg.node_id);
      }
    };

    return () => ws.close();
  }, []);

  const handleNodeClick = (node) => {
    // Tell the TUI we are focusing on this node
    const ws = new WebSocket('ws://localhost:8000/ws');
    ws.send(JSON.stringify({ type: 'focus_node', node_id: node.id }));
    ws.close();
  };

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#000' }}>
      <ForceGraph3D
        ref={fgRef}
        graphData={graphData}
        nodeAutoColorBy="type"
        nodeLabel="label"
        onNodeClick={handleNodeClick}
        nodeCanvasObject={(node, ctx, globalScale) => {
          // Custom rendering for "Neural" feel
          const r = node.val * 2;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
          ctx.fillStyle = node.color;
          ctx.fill();
        }}
      />
      <div style={{
        position: 'absolute', top: 20, left: 20, color: 'cyan',
        fontFamily: 'monospace', pointerEvents: 'none'
      }}>
        <h1>TALOS NEURAL ATLAS</h1>
        <p>Focus: {focusNode || 'Scanning...'}</p>
      </div>
    </div>
  );
};

export default TalosAtlas;
