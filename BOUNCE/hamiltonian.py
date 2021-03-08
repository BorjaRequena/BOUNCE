# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/03_hamiltonian.ipynb (unless otherwise specified).

__all__ = ['Hamiltonian1D', 'XYHamiltonian', 'XXHamiltonian']

# Cell
import numpy as np
import picos
import networkx as nx
import matplotlib.pyplot as plt

# Cell
class Hamiltonian1D:

    x = picos.Constant('x', [[0, 1], [1, 0]])
    y = picos.Constant('y', [[0, -1j], [1j, 0]])
    z = picos.Constant('z', [[1, 0], [0, -1]])
    Id = picos.Constant('Id', [[1, 0], [0, 1]])

    def __init__(self, N, linear, quadratic):
        self.N = N
        self.linear = linear
        self.quadratic = quadratic

    def draw_system(self, figsize=(8,6), cmap=plt.cm.plasma):
        "Conceptual drawing of the system showing interaction strength and on-site field."
        G = nx.Graph()
        G.add_nodes_from([(node,{'w': w}) for node,w in zip(np.arange(self.N),self.linear)])
        G.add_edges_from([(n,n+1,{'w': w}) if n<self.N-1 else (n,0,{'w': w}) for n,w in zip(np.arange(self.N), self.quadratic)])
        plt.figure(figsize=figsize)
        pos = nx.circular_layout(G)
        for (n, d) in G.nodes(data=True):
            nx.draw_networkx_nodes(G, pos=pos, nodelist=[n], node_size=400, node_color=[d['w']/np.max(self.linear)],
                                   cmap=cmap, vmin=0, vmax=1)
        for (u,v,d) in G.edges(data=True):
            nx.draw_networkx_edges(G, pos=pos, edgelist=[(u,v)], width=5, alpha=d['w']/np.max(self.quadratic))
        d = np.array([-0.1, 0])
        label_pos = {k: v+d if v[0]<0 else v-d for k,v in pos.items()}
        nx.draw_networkx_labels(G, pos=label_pos, font_size=25);


# Cell
class XYHamiltonian(Hamiltonian1D):
    def __init__(self, N, linear, quadratic, g):
        super().__init__(N, linear, quadratic)
        self.g = g
        self.model = 'xy'

    def to_sdp(self):
        "Returns hamiltonian in terms of SDP variables."
        linear = [(np.array([i]), self.linear[i]*self.z) for i in range(self.N)]
        quadratic = [(np.sort(np.array([i, (i+1)%self.N])), self._2body_interaction(i)) for i in range(self.N)]
        return linear + quadratic

    def _2body_interaction(self, i):
        return self.quadratic[i]*((1+self.g)*self.x@self.x + (1-self.g)*self.y@self.y)

# Cell
class XXHamiltonian(Hamiltonian1D):
    def __init__(self, N, linear, quadratic):
        super().__init__(N, linear, quadratic)
        self.model = 'xx'

    def to_sdp(self):
        "Returns hamiltonian in terms of SDP variables."
        linear = [(np.array([i]), self.linear[i]*self.z) for i in range(self.N)]
        quadratic = [(np.sort(np.array([i, (i+1)%self.N])), self._2body_interaction(i)) for i in range(self.N)]
        return linear + quadratic

    def _2body_interaction(self, i):
        return self.quadratic[i]*(self.x@self.x + self.y@self.y)