# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/06_sdp.ipynb (unless otherwise specified).

__all__ = ['solve_sdp', 'complementary_system', 'picos2np', 'ojimetro']

# Cell
import picos
import numpy as np
from .utils import state2str, simplify_layout

# Cell
def solve_sdp(layout, hamiltonian):
    "Solves the SDP defined by the given layout and Hamiltonian."
    layout = simplify_layout(layout)
    H = hamiltonian.to_sdp()
    problem = picos.Problem(solver = 'cvxopt')
    variables = [(site, picos.HermitianVariable('rho'+','.join(map(str, site)), (2**len(site), 2**len(site)))) for site in layout]
    problem.add_list_of_constraints([rho >> 0 for _, rho in variables])
    problem.add_list_of_constraints([picos.trace(rho) == 1 for _, rho in variables])

    # Energy
    objective = 0
    for support, h in H:
        supported = False
        for sites, rho in variables:
            common, _, idx = np.intersect1d(support, sites, return_indices=True)
            if len(common) == len(support):
                rdm = rho.partial_trace(complementary_system(idx, len(sites)))
                objective += (rdm | h) # Tr(rdm·H')
                supported = True
                break
        if not supported:
            eigenvalues, _ = np.linalg.eigh(picos2np(h))
            objective += min(eigenvalues)

    problem.set_objective('min', objective)

    # Compatibility
    compatibility_constraints = []
    for k1, (sites1, rho1) in enumerate(variables):
        for k2 in range(k1+1, len(variables)):
            sites2, rho2 = variables[k2]
            common, idx1, idx2 = np.intersect1d(sites1, sites2, return_indices=True)
            if len(common) > 0:
                partial_trace1 = rho1.partial_trace(complementary_system(idx1, len(sites1)))
                partial_trace2 = rho2.partial_trace(complementary_system(idx2, len(sites2)))
                constraint = partial_trace1 - partial_trace2 == 0
                compatibility_constraints.append(constraint)

    problem.add_list_of_constraints(compatibility_constraints)

    try:
        problem.solve()
        result = np.real(objective.value)
    except:
        print(problem)
        result = 0.
    return result

def complementary_system(subsystem, size):
    "Obtains the complementary system of subsystem."
    return list(map(int, np.setdiff1d(np.arange(size), subsystem)))

def picos2np(variable):
    "Converts picos variable (even sparse) to numpy matrix."
    return picos.expressions.data.cvx2np(variable.value)

# Cell
def ojimetro(L):
    "Estimates the amount of free parameters in the SDP associated to the layout."
    # The old blocks are len(L)
    L = simplify_layout(L)
    all_variables = np.sum([2**(2*len(sites)) for sites in L])
    intersections = []
    for k, sites1 in enumerate(L[:-1]):
        for sites2 in L[k+1:]:
            intersections.append(np.intersect1d(sites1, sites2))
    intersections = simplify_layout(intersections)
    dep_variables = np.sum([2**(2*len(sites)) for sites in intersections])
    return all_variables-dep_variables