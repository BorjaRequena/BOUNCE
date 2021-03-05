# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/00_environment.ipynb (unless otherwise specified).

__all__ = ['SDPEnvironment']

# Cell
import numpy as np
import torch
import itertools
from copy import deepcopy
from pathlib import Path
import pickle

from .sdp import solve_sdp, ojimetro
from .utils import state2int, state2str, contained_constraints, simplify_layout, fill_layout, dist_poly

# Cell
class SDPEnvironment:
    "Environment for constraint exploration."

    def __init__(self, N, H, param_profile, reward_criterion="energy_norm", energy_threshold=1e-3):

        self.N = N # Number of sites
        self.H = H # Hamiltonian

        # Parameter profile
        self.param_profile = param_profile
        self.param_limit = param_profile(0)

        # Reward function
        self.reward_fun = getattr(self, reward_criterion+"_reward")
        self.dist_d = 5

        # Memory of visited states. It is a lookup table for computation speedup.
        self.memory_limit = 1e6
        self._get_memory()

        # Creating the agent basis
        self.agent_basis = self._get_agent_basis()
        self._get_layout_basis()
        self._constrain_basis()

        # Initialize the environment
#         self._find_initial_state()
        self.reset()

        # Memories
        self.E_threshold = energy_threshold
        self.max_energy  = -np.inf  # Maximum energy ever obtained
        self.min_energy  = np.inf   # Minimum energy ever obtained
        self.max_params  = -np.inf  # Maximum amount of parameters ever obtained
        self.min_params  = np.inf   # Minimum amount of parameters ever obtained
        self.best = np.array([-np.inf, np.inf, -np.inf])  # Maximum energy, best and worst params
        self.best_layout = deepcopy(self.layout)

        # Initial state reference
        energy, params, err = self.get_values()
        if not err: self._min_max_update(energy, params)
        else:       raise ValueError(f"Something went wrong. Initial state {self.state} provides error.")


    def reset(self):
        ''' Resets the environment. State goes either to random or to all zeros.
        Outputs: - State in env form. '''
        # Reset state with lowest energy state
        self.state = np.zeros(len(self.layout_basis), dtype=int)
#         self.state[:self.N] = 1
        return self.state

    @property
    def constraints(self):
        return [self.state[i:i+self.N] for i in range(0, len(self.state), self.N)]

    @property
    def layout(self):
        layout = simplify_layout([np.array(sites) for sites in self.layout_basis[self.state.astype(bool)]])
        return fill_layout(layout, self.N)

    def show_constraints(self, state=None):
        if state is None: state = self.state
        sc = [state[i:i+self.N] for i in range(0, len(state), self.N)]
        for k,c in enumerate(sc): print(f"{k+2}: {np.array2string(c)[1:-1]}")

    ## agent - environment interaction ##
    def perform_action(self, actions, it):
        "Receives a list of actions (priority ordered) and repeatedly tries to execute them until one is accepted."
        state_0 = deepcopy(self.state)
        for a in actions:
            next_state, energy, params, err  = self.explorative_step(a, it) # Try action
            if err:
                self.state = deepcopy(state_0)
                _, _, err = self.get_values()
                if err: raise Exception(f"Error found undoing an action. Going back from "+
                                        f"{next_state} to {self.state} with action {a}. Ref state is {state_0}")
            else:
                break
#         _, next_state, _ = self._simplify_constraints() # In case we want to try with simplified states on the NN
        return next_state, a, energy, params, err

    def explorative_step(self, action, it):
        ''' Perform action over the current state and calculate/recalls the features
        of the resulting state. Then calculates the reward.
        Inputs: - action: integer indicating the index of the state to be flipped
                - it: training iteration (episode in main code)
        Outputs: - Resulting state
                 - Parameters of the SDP:
                    - Parameters given the new constraints
                    - Energy of the new state
                    - Error'''

        if action < len(self.state):   self.state[action] = -self.state[action] + 1
        elif action > len(self.state): raise ValueError(f"Action {action} exceeds maximum index {len(self.state)}")
        # Case that action == len(self.state) the action is to remain in the current state

        self.state[contained_constraints(self.state, self.N)] = 1 # Include the smaller contained constraints
        self.param_limit = self.param_profile(it)
        energy, params, err = self.get_values() # Calculate the features
        if not err: self._min_max_update(energy, params)

        return self.state, energy, params, err


    ## Reward functions ##
    def energy_reward(self, energies, parameters, best_ref=None):
        "The reward is the energy of the state."
        best = self.best if best_ref is None else best_ref
        thresh_mask = torch.abs(energies - best[0]) < self.E_threshold
        reward = deepcopy(energies)
        reward[energies == 0] = self.min_energy*1.1                   # Errors
        reward[parameters > self.param_limit] = self.min_energy*1.1   # Over parameter limit
        reward[thresh_mask] = parameters[thresh_mask]/best[2].float() # Reweight threshold states
        return reward

    def energy_norm_reward(self, energies, parameters, best_ref=None):
        "The reward is a normalized function from 0 to 1 as function of the energy and parameters."
        best = self.best if best_ref is None else best_ref
        thresh_mask = torch.abs(energies - best[0]) < self.E_threshold
        reward = dist_poly(deepcopy(energies), best[0], self.min_energy, d=self.dist_d)
        reward[energies == 0] = 0                                     # Errors
        reward[parameters > self.param_limit] = 0                     # Over parameter limit
        reward[thresh_mask] = best[2]/parameters[thresh_mask].float() # Reweight threshold states
        return reward*best[1]/best[2]

    def energy_improve_reward(self, energies, parameters, best_ref=None):
        "The reward is the energy improvement (+1) with respect to the minimum energy."
        best = self.best if best_ref is None else best_ref
        thresh_mask = torch.abs(energies - best[0]) < self.E_threshold
        reward = energies - self.min_energy + 1
        reward[energies == 0] = 0                                     # Errors
        reward[parameters > self.param_limit] = 0                     # Over parameter limit
        reward[thresh_mask] = best[2]/parameters[thresh_mask].float() # Reweight threshold states
        return reward

    ## SDP results ##
    def get_values(self):
        "Solve the associated SDP to the state and return the results."
        binary = state2int(self.state)
        if binary in self.memory.keys():
            energy, params, err = self._remember(binary)
            energy, params, err = self._check_current_limit(binary, energy, params, err)
        else:
            energy, params, err = self.get_sdp_results()
            if len(self.memory) < self.memory_limit:
                self._memorize(binary, [energy, params, err])

        return energy, params, err

    def get_params(self):
        "Estimates the free parameters needed to solve the SDP"
        binary = state2int(self.state)
        if binary in self.memory.keys(): _, params, _ = self._remember(binary)
        else:                            params = ojimetro(self.layout)
        return params

    def get_sdp_results(self):
        "Computes the energy bound solving the associated SDP to the sate"
        energy = solve_sdp(self.layout, self.H)
        params = ojimetro(self.layout)
        if energy == 0:                 err = 1
        elif params > self.param_limit: err = 2
        else:                           err = 0
        return energy, params, err


    def _SDP_graph(self, L=None):
        """SDP speedup for graph states by avoiding matlab queries. L is optional input for generality purposes, does not
        even need simplified versions, matlab will do with the simplification."""
        err = self._check_support(3, L=L)
        if err: return 0, 0, err, 0.
        else:
            params = self.get_params() # visits matlab
            if params > self.param_limit: return params, len(self.L)-1, 2, 0.   # Error for excess of parameters (arbitrary numer of blocks)
            else:                        return params, len(self.L)-1, err, 1. # SDP values (arbitrary number of blocks)

    def _check_support(self, min_support, L=None):
        """L is optional input for generality purposes"""
        self.L = self.matlab_basis[self.state.astype(bool) if L is None else L.astype(bool)]
        str_layout = '|'.join(map(lambda x: ' '.join([str(x[i]) for i in range(len(x))]), self.L))

        err = 0
        for constraint in self.agent_basis[min_support-2]:
            if constraint not in str_layout: err = 1; break
        return err

    def _check_current_limit(self, binary, energy, params, err):
        if not err and params > self.param_limit:
            # Pre-computed parameters are larger than current limit
            err, energy = 2, 0.
        elif err == 2 and params <= self.param_limit or err==1:
            # If the error was due to excess of parameters but it fits now, recompute the SDP
            energy, params, err = self.get_sdp_results()
            self._memorize(binary, [energy, params, err])

        return energy, params, err

    def _min_max_update(self, energy, params):
        """Given a a new obtained set of energy and parameters, check whether they are higher or lower than the max and min
        values obtained previously and update them."""
        if params < self.min_params: self.min_params = params
        if params > self.max_params: self.max_params = params
        if energy < self.min_energy: self.min_energy = energy
        if energy > self.max_energy: self.max_energy = energy

        # Recall in self.best we have [best_energy, best_params, worst_params]
        if energy > self.best[0] and np.abs(energy-self.best[0]) > self.E_threshold:
            # If energy beyond threshold, keep it all
            self.best = np.array([energy, params, params])
            self.best_layout = deepcopy(self.layout)

        elif np.abs(energy-self.best[0]) < self.E_threshold:
            # If energy within threshold
            if   params < self.best[1]:
                self.best[0], self.best[1] = energy, params
                self.best_layout = deepcopy(self.layout)
            elif params > self.best[2]:
                self.best[2] = params

    ## State vector methods ##
    def _layout2state(self, L=None):
        "Formats layout to state"
        if L is None: L = self.layout
        state = np.zeros(self.agent_basis.shape)
        for constraint in L:
            idx1 = len(constraint) - 2
            const = np.sort(constraint)
            const_str = ' '.join(map(str, const))
            idx2 = np.where(const_str == self.agent_basis[idx1])[0]
            state[idx1, idx2] = 1
        return state.reshape(self.state.shape)

    def _simplify_constraints(self):
        '''Simplifies current state removing contained constraints.'''
        state_simp = deepcopy(self.state)
        state_simp[contained_constraints(state_simp, self.N)] = 0
        return state_simp, state2int(state_simp)

    ## Memory methods ##
    def save_memory(self):
        old_memory = self._read_memory()
        full_memory = {**old_memory, **self.memory}
        with open(self.memory_path, "wb") as f:
            pickle.dump(full_memory, f, protocol=pickle.HIGHEST_PROTOCOL)
        self.memory = self._read_memory()

    def _get_memory(self):
        "Reads the corresponding memory file"
        memory_dir = Path("../memories/")
        if self.H.model == "graph":
            self.memory_path = memory_dir/f"env_memory_{self.H.model}_N{self.N}.pkl"
        elif self.H.model == "xy":
            self.memory_path = memory_dir/(f"env_memory_{self.H.model}_g{self.H.g}_N{self.N}" +
                                    f"_B{state2str(self.H.linear)}_J{state2str(self.H.quadratic)}.pkl")
        else:
            self.memory_path = memory_dir/(f"env_memory_{self.H.model}_N{self.N}" +
                                    f"_B{state2str(self.H.linear)}_J{state2str(self.H.quadratic)}.pkl")
        self.memory = self._read_memory()

    def _memorize(self, constraint, values):
        "Add to memory the states visited and the values of the SDP for each iteration"
        energy, params, err = values

        if constraint in self.memory.keys() and params > self.param_limit and err != 2:
            _, _, old_err = self._remember(constraint)
            if old_err != 1:
                raise Exception(f"Trying to memorize constraint with binary index {constraint} already in memory")
        elif not isinstance(constraint, int):
            raise ValueError(f"Constraint is not a binary integer {constraint}")
        else:
            self.memory[constraint] = values

    def _remember(self, constraint):
        "Given a set of constraint, outputs the values of the SDP."
        return self.memory[constraint]

    def _read_memory(self):
        try:
            with open(self.memory_path, "rb") as f:
                memory = pickle.load(f)
        except: memory = {}
        return memory

    ## Agent action basis methods ##
    def _get_agent_basis(self, local_hamiltonian = True):
        "Creates the basis of the different possible constraints given as a list of str"
        if local_hamiltonian: # only nearest neigbors
            s = ""
            agent_basis = []
            for n1 in range(1,self.N-1): # Avoid computing 1-body and full system
                aa = []
                for n2 in range(self.N):
                    l_c = np.arange(n1+1)+n2
                    l_c[l_c >= self.N] -= self.N
                    l_c = np.sort(l_c)
                    num = ' '.join(map(str,l_c))
                    aa.append(s.join(num))
                _, idx = np.unique(aa, return_index=True)
                aa = np.array(aa)[np.sort(idx)].tolist()
                agent_basis.append(aa)
        else: # arbitrary connections
            num = ' '.join(map(str,list(range(self.N))))
            s = ""
            agent_basis = []
            for nn in range(self.N):
                ll  = np.sort(list(itertools.permutations(num, nn+1)))
                aa = []
                for l in ll:
                    seq = l
                    aa.append(s.join(seq))
                agent_basis.append(np.unique(aa))
        return np.array(agent_basis)

    def _get_layout_basis(self):
        "Builds layout basis."
        a = [item for sublist in self.agent_basis for item in sublist]
        self.layout_basis = []
        for item in a:
            self.layout_basis.append([int(s) for s in item.split(sep=" ") if s.isdigit()])
        self.layout_basis = np.array(self.layout_basis)

    def _constrain_basis(self):
        """Given the maximum allowed of parameters, this function redefines the basis
        by cutting down the unaccessible states from the state-vector"""
        for k in range(self.N-2):
            self.state = np.zeros(len(self.layout_basis))
#             self.state[:self.N] = 1
            self.state[k*self.N] = 1
            p = self.get_params()
            if p > self.param_profile.max_params:
                self.agent_basis = self.agent_basis[:k]
                self._get_layout_basis()
                break

        if k == 0:
            raise ValueError(f"Unable to fit minimum complexity state with {int(p)} parameters "+
        f"for maximum allowed {self.param_profile.max_params}. Will need beefier computers for this!")

    def _find_initial_state(self):
        """Finds a computable initial state. Starts with the lowest interaction and increases
        it until it finds a suitable initial state."""
        self.n = 0
        err = 1
        while err:
            self.n += 1
            self.state = np.zeros(len(self.layout_basis))
            self.state[:self.n*self.N] = 1
            _, params, _, err, _ = self.get_values()
            if err == 2: raise ValueError("Unable to find computable initial state within parameter limit "+
                                          f"{self.param_limit}. Currently trying with groups of {self.n+1} requiring "+
                                          f"{params} parameters. Try increasing the computational budget.")

    def _get_constraint_bounds(self):
        "Finds the maximum amount of each constraint that can be fit into the budget."
        self.max_const_by_size = [self.N]
        for size in range(1, len(self.agent_basis)):
            for k in range(self.N):
                self.state = np.zeros(len(self.layout_basis))
                self.state[:self.N] = 1 # All pairs must fit for sure
                self.state[size*self.N:size*self.N + k] = 1
                params = self.get_params()
                if params > self.param_profile.max_params: k-=1; break
            self.max_const_by_size.append(k+1)