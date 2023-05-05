from itertools import compress, chain, combinations
import numpy as np
import jax.numpy as jnp
import pandas as pd
import regularized_optimization as reg_opt
import scipy.optimize as opt 

def state_space(n: int) -> np.array:
    """
    Generates all possible states of size n in lexicographic order
    Args:
        n (int): total number of events
    Returns:
         np.array: complete statespace
    """
    states = np.arange(2**n, dtype=np.uint8).reshape((2**n, 1))
    ret = np.unpackbits(states, axis=1, count=n, bitorder="little")
    return ret #np.array([f'{i:0b}'.zfill(n)[::-1] for i in range(2**n)])


def trunk_states(state: np.array) -> np.array:
    """
    Enumerates all possible states that a tumor(pair) with genotype(s) "state" could have visited
    Args:
        state (np.array): Bitstring, genotype of a tumor(pair) of a single patient
    Returns:
        np.array
    """
    n = state.size
    inds = np.ones(2**n, dtype=np.uint8)
    for i in range(n):
        if state[i] == 1:
            inds[0:2**(i+1)] = np.kron(np.array([1, 1]), inds[0:2**i])
        else:
            inds[0:2**(i+1)] = np.kron(np.array([1, 0]), inds[0:2**i])
    
    return state_space(n)[inds.astype(bool), :]


def ssr_to_fss(state: np.array) -> np.array:
    """This gives the indices of the rate matrix that are appearing in the
    state space restricted rate matrix.

    Args:
        state (np.array): Binary state vector, representing the current sample's events.
    Returns:
        np.array: Indices of the rate matrix.
    """
    res = np.ones(1)
    for s in state:
        res = np.kron(np.array([1, s]), res)
    return res.astype(bool)


def random_theta(n: int, sparsity: float) -> np.array:
    """
    Generates a logarithmic theta with normal distributed entries
    Args:
        n (int): Number of mutations
        sparsity (float): Percentage of zero entries in theta
    returns:
        np.array: theta
    """
    npone = n + 1
    log_theta = np.zeros((npone, npone))
    log_theta += np.diag(np.random.normal(size=npone))
    index = np.argwhere(log_theta == 0)[
        np.random.choice(npone**2-npone, size=int((npone**2-npone)
                         * (1-sparsity)), replace=True)
    ]
    log_theta[index[:, 0], index[:, 1]] = np.random.normal(
        size=int((npone**2-npone)*(1-sparsity)))
    return log_theta


def reachable_states(n: int):

    reachable = np.zeros(2**(2*n + 1))

    for i in chain.from_iterable(combinations(list(range(n)), r) for r in range((n+1))):
        reachable[sum((2**(2*j))*3 for j in i)] = 1

    reachable[2**(2*n):] = 1
    return reachable.astype(bool)


def finite_sample(pTh: np.array, k: int) -> np.array:
    """
    Generates k random samples drawn from a probability distribution pTh
    Code taken from https://github.com/spang-lab/LearnMHN/blob/main/mhn/original/UtilityFunctions.py
    Args:
        pTh (np.array): Probability distribution to draw samples from
        k (int): Number of samples to draw
    Returns:
         np.array: Subsampled probability distribution
    """
    n = pTh.size
    return np.bincount(np.random.choice(n, k, replace=True, p=pTh), minlength=n) / k


def diagnosis_theta(log_theta: np.array, log_diag_rate: float = 0) -> np.array:
    """
    transforms a logarithmic theta in order to be used for the diagnosis MHN
    Args:
        log_theta (np.array): theta matrix with logarithmic entries
        log_diag_rate (float): logarithmic base rate of the diagnosis event
    Returns:
         (n+1)x(n+1) dimensional theta matrix
    """
    n = log_theta.shape[0] - 1
    theta_diag = np.zeros(shape=(n + 2, n + 2), dtype=float)
    theta_diag[0:n, 0:n] = log_theta[0:n, 0:n]
    theta_diag[-1, 0:n] = log_theta[n, 0:n]
    theta_diag[0:n, -1] = log_theta[0:n, n]
    theta_diag[0:n + 2, -2] = np.array([-np.inf] * (n + 2))
    theta_diag[n, n] = log_diag_rate
    theta_diag[n + 1, n + 1] = log_theta[n, n]
    return theta_diag

def marginalize(p_in: np.array, n: int, marg_met: bool=True) -> np.array:
    """
    Returns the marginal distribution of a joint distribution of primary tumors and metastases wrt. to the tumor type
    Args:
        p_in (np.array): Joint distribution to marginalise
        n (int): number of genomic events
        marg_met (bool): If true: marginalize over metastases, else: marginalize over primaries
    Returns:
         np.array: 2^n dimensional marginal distribution
    """
    p = p_in.copy()
    for _ in range(n):
        p = p.reshape((-1, 4), order="C")
        if marg_met:
            y = np.column_stack((p[:, 0] + p[:, 2], p[:, 1] + p[:, 3]))
        else:
            y = np.column_stack((p[:, 0] + p[:, 1], p[:, 2] + p[:, 3]))
        p = y.flatten(order="F")

    p = p.reshape((-1, 2), order="C")
    return p.flatten(order="F")

def ssr_marginalize(p_in: np.array, n: int, state: np.array, marg_met: bool=True, marg_seeding: bool=False) -> np.array:
    """
    Returns the marginal distribution of a joint distribution of primary tumors and metastases wrt. to the tumor type
    Args:
        p_in (np.array): Joint distribution to marginalise
        n (int): number of genomic events
        state (np.array): Binary state vector, representing the current sample's events
        marg_met (bool): If true: marginalize over metastases, else: marginalize over primaries
        marg_seeding (bool): If true marginalize over the status of the seeding event
    Returns:
         np.array: 2^m dimensional marginal distribution
    """
    p = p_in.copy()
    for i in range(n):
        mut = state[2 * i: 2 * i + 2]
        muts = mut.sum()

        if muts == 0:               
            pass
        # Prim[i] = 1 and Met[i] = 0 and marg. over mets
        # Or Prim[i] = 0 and Met[i] = 1 and marg. over prims
        elif (mut[0] == 0 and not marg_met) or (mut[1] == 0 and marg_met):
            p = p.reshape((-1,2), order="C").ravel(order="F")
    
        # Prim[i] = Met[i] = 1
        elif muts == 2:
            p = p.reshape((-1, 4), order = "C")
            if marg_met:
                 # Marg. over mets
                y = np.column_stack((p[:, 0] + p[:, 2], p[:, 1] + p[:, 3]))
            else:
                # Marg. over prims
                y = np.column_stack((p[:, 0] + p[:, 1], p[:, 2] + p[:, 3]))
            p = y.ravel(order="F")
        # Prim[i] = 0 and Met[i] = 1 and marg. over mets
        # Or Prim[i] =1 and Met[i] = 0 and marg. over prims
        else: 
            p = p.reshape((-1, 2), order = "C")
            p = p[:, 0] + p[:, 1]
            p = p.ravel(order="F")

    if state[-1] == 1 and marg_seeding:
        p = p.reshape((-1, 2), order="C")
        p = p[:, 0] + p[:, 1]
        p = p.ravel(order="F")
    else:
        p =  p.reshape((-1,2)).ravel(order="F")
    return p


def ssr_marginalize_mat_vec(p_in: np.array, n: int, state: np.array, marg_met: bool=True, marg_seeding: bool=False) -> np.array:
    """
    Returns the marginal distribution of a joint distribution of primary tumors and metastases wrt. to the tumor type
    Args:
        p_in (np.array): Joint distribution to marginalise
        n (int): number of genomic events
        state (np.array): Binary state vector, representing the current sample's events
        marg_met (bool): If true: marginalize over metastases, else: marginalize over primaries
        marg_seeding (bool): If true marginalize over the status of the seeding event
    Returns:
         np.array: 2^m dimensional marginal distribution
    """

    met_mat = np.array([[1,0], [0,1],[1,0],[0,1]])
    prim_mat = np.array([[1,0], [1,0],[0,1],[0,1]])
    one_mat = np.array([[1], [1]])

    p = p_in.copy()
    for i in range(n):
        mut = state[2 * i: 2 * i + 2]
        muts = mut.sum()

        if muts == 0:               
            pass
        # Prim[i] = 1 and Met[i] = 0 and marg. over mets
        # Or Prim[i] = 0 and Met[i] = 1 and marg. over prims
        elif (mut[0] == 0 and not marg_met) or (mut[1] == 0 and marg_met):
            p = p.reshape((-1,2), order="C").ravel(order="F")
    
        # Prim[i] = Met[i] = 1
        elif muts == 2:
            p = p.reshape((-1, 4), order = "C")
            if marg_met:
                 # Marg. over mets
                #y = np.column_stack((p[:, 0] + p[:, 2], p[:, 1] + p[:, 3]))
                y = p @ met_mat
            else:
                # Marg. over prims
                #y = np.column_stack((p[:, 0] + p[:, 1], p[:, 2] + p[:, 3]))
                y = p @ prim_mat
            p = y.ravel(order="F")
        # Prim[i] = 0 and Met[i] = 1 and marg. over mets
        # Or Prim[i] =1 and Met[i] = 0 and marg. over prims
        else:
            p = p.reshape((-1, 2), order = "C") 
            p = p @ one_mat
            p = p.ravel(order="F")

    if state[-1] == 1 and marg_seeding:
        p = p.reshape((-1, 2), order="C")
        p = p @ one_mat
        p = p.ravel(order="F")
    else:
        p = p.reshape((-1,2)).ravel(order="F")
    return p

def ssr_marginalize_mat_vec_transp(p_in: np.array, n: int, state: np.array, marg_met: bool=True, marg_seeding: bool=False) -> np.array:
    """
    Returns the marginal distribution of a joint distribution of primary tumors and metastases wrt. to the tumor type
    Args:
        p_in (np.array): Joint distribution to marginalise
        n (int): number of genomic events
        state (np.array): Binary state vector, representing the current sample's events
        marg_met (bool): If true: marginalize over metastases, else: marginalize over primaries
        marg_seeding (bool): If true marginalize over the status of the seeding event
    Returns:
         np.array: 2^m dimensional marginal distribution
    """
    
    met_mat = np.array([[1, 0, 1, 0], [0, 1, 0, 1]])
    prim_mat = np.array([[1, 1, 0, 0 ], [0, 0, 1, 1]])
    one_mat = np.array([[1, 1]])

    p = p_in.copy()
    for i in range(n):
        mut = state[2 * i: 2 * i + 2]
        muts = mut.sum()

        if muts == 0:               
            pass
        # Prim[i] = 1 and Met[i] = 0 and marg. over mets
        # Or Prim[i] = 0 and Met[i] = 1 and marg. over prims
        elif (mut[0] == 0 and not marg_met) or (mut[1] == 0 and marg_met):
            p = p.reshape((-1,2), order="C").ravel(order="F")
    
        # Prim[i] = Met[i] = 1
        elif muts == 2:
            p = p.reshape((-1, 2), order = "C")
            if marg_met:
                 # Marg. over mets
                #y = np.column_stack((p[:, 0] + p[:, 2], p[:, 1] + p[:, 3]))
                y = p @ met_mat
            else:
                # Marg. over prims
                #y = np.column_stack((p[:, 0] + p[:, 1], p[:, 2] + p[:, 3]))
                y = p @ prim_mat
            p = y.ravel(order="F")
        # Prim[i] = 0 and Met[i] = 1 and marg. over mets
        # Or Prim[i] =1 and Met[i] = 0 and marg. over prims
        else:
            p = p.reshape((-1, 1), order = "C") 
            p = p @ one_mat
            p = p.ravel(order="F")

    if state[-1] == 1 and marg_seeding:
        p = p.reshape((-1, 1), order="C")
        p = p @ one_mat
        p = p.ravel(order="F")
    else:
        p = p.reshape((-1,2)).ravel(order="F")
    return p

def ssr_obs_dist(p_in: np.array, state: np.array, n: int, obs_prim: bool=True) -> np.array:
    """
    Returns P(Prim = prim_obs, Met) or P(Prim, Met = met_obs), the joint distribution evaluated at either
    the observed metastasis state or the observed primary tumor state
    Args:
        p_in (np.array): Joint probability distribution of prims and mets
        state (np.array): bitstring, mutational state of prim and met of a patient
        n (int): total number of genomic events
        obs_prim (bool): If true return P(Prim = prim_obs, Met) else return P(Prim, Met = met_obs)
    Returns:
        np.array
    """
    p = p_in.copy()
    for i in range(n):
        mut = state[2*i] + 2 * state[2*i+1]
        print(mut)
        if mut == 0:
            pass
        elif (mut == 1 and obs_prim) or (mut == 2 and not obs_prim):
            p = p.reshape((-1, 2), order="C")
            p = p[:,1]
            p = p.ravel(order="F")
        elif (mut == 2 and obs_prim) or (mut == 1 and not obs_prim):
            p = p.reshape((-1, 2), order="C")
            p = p.ravel(order="F")
        else:
            p = p.reshape((-1, 4), order="C")
            if obs_prim:
                p = np.column_stack([p[:,1], p[:,3]])
            else:
                p = np.column_stack([p[:,2], p[:,3]])
            p = p.ravel(order="F")
    if state[-1] == 1:
        p = p.reshape((-1, 2), order="C")
        p = p.ravel(order="F")
    else:
        pass
    return p

def ssr_obs_dist_mat_vec(p_in: np.array, state: np.array, n: int, obs_prim: bool=True) -> np.array:
    """
    Returns P(Prim = prim_obs, Met) or P(Prim, Met = met_obs), the joint distribution evaluated at either
    the observed metastasis state or the observed primary tumor state
    Args:
        p_in (np.array): Joint probability distribution of prims and mets
        state (np.array): bitstring, mutational state of prim and met of a patient
        n (int): total number of genomic events
        obs_prim (bool): If true return P(Prim = prim_obs, Met) else return P(Prim, Met = met_obs)
    Returns:
        np.array
    """
    p = p_in.copy()
    for i in range(n):
        mut = state[2*i] + 2 * state[2*i+1]
        print(mut)
        if mut == 0:
            pass
        elif (mut == 1 and obs_prim) or (mut == 2 and not obs_prim):
            p = p.reshape((-1, 2), order="C")
            p = p @ np.array([[0],[1]])
            p = p.ravel(order="F")
        elif (mut == 2 and obs_prim) or (mut == 1 and not obs_prim):
            p = p.reshape((-1, 2), order="C")
            p = p.ravel(order="F")
        else:
            p = p.reshape((-1, 4), order="C")
            if obs_prim:
                #p = np.column_stack([p[:,1], p[:,3]])
                p = p @ np.array([[0,0], [1,0], [0,0], [0,1]])
            else:
                #p = np.column_stack([p[:,2], p[:,3]])
                p = p @ np.array([[0,0], [0,0], [1,0], [0,1]])
            p = p.ravel(order="F")
    if state[-1] == 1:
        p = p.reshape((-1, 2), order="C")
        p = p.ravel(order="F")
    else:
        pass
    return p

def ssr_obs_dist_mat_vec_transp(p_in: np.array, state: np.array, n: int, obs_prim: bool=True) -> np.array:
    """
    Returns P(Prim = prim_obs, Met) or P(Prim, Met = met_obs), the joint distribution evaluated at either
    the observed metastasis state or the observed primary tumor state
    Args:
        p_in (np.array): Joint probability distribution of prims and mets
        state (np.array): bitstring, mutational state of prim and met of a patient
        n (int): total number of genomic events
        obs_prim (bool): If true return P(Prim = prim_obs, Met) else return P(Prim, Met = met_obs)
    Returns:
        np.array
    """
    p = p_in.copy()
    for i in range(n):
        mut = state[2*i] + 2 * state[2*i+1]
        print(mut)
        if mut == 0:
            pass
        elif (mut == 1 and obs_prim) or (mut == 2 and not obs_prim):
            p = p.reshape((-1, 1), order="C")
            p = p @ np.array([[0, 1]])
            p = p.ravel(order="F")
        elif (mut == 2 and obs_prim) or (mut == 1 and not obs_prim):
            p = p.reshape((-1, 2), order="C")
            p = p.ravel(order="F")
        else:
            p = p.reshape((-1, 2), order="C")
            if obs_prim:
                #p = np.column_stack([p[:,1], p[:,3]])
                p = p @ np.array([[0, 1, 0, 0], [0, 0, 0, 1]])
            else:
                #p = np.column_stack([p[:,2], p[:,3]])
                p = np.array([[0, 0, 1, 0], [0, 0, 0, 1]])
            p = p.ravel(order="F")
    if state[-1] == 1:
        p = p.reshape((-1, 2), order="C")
        p = p.ravel(order="F")
    else:
        pass
    return p

def split_data(dat: pd.DataFrame):
    prim_only =  dat.loc[(0, "absent")].to_numpy(dtype=np.int8)
    met_only = dat.loc[(0, "isMetastasis")].to_numpy(dtype=np.int8)
    prim_met = dat.loc[(0, "present")].to_numpy(dtype=np.int8)
    coupled = dat.loc[(1, "isPaired")].to_numpy(dtype=np.int8)
    coupled = coupled[coupled.sum(axis=1) > 1, ]
    return jnp.array(prim_only), jnp.array(met_only), jnp.array(prim_met), jnp.array(coupled)


def cross_val(dat: pd.DataFrame, splits: jnp.array, nfolds: int, start_params: jnp.array, m_p_corr: float, n: int) -> float:
    ndat = dat.shape[0]
    dat = dat.reset_index()
    shuffled = dat.sample(frac=1)
    runs = np.zeros((nfolds, splits.shape[0]))
    batch_size = np.ceil(ndat/nfolds)
    for i in range(nfolds):
        start = batch_size*i
        stop = np.min((batch_size*(i+1), ndat))

        train_inds = np.concatenate((np.arange(start), np.arange(stop, ndat)))
        train = shuffled.iloc[train_inds,:]
        train = train.set_index(["paired", "metaStatus"])
        train_prim_only, train_met_only, train_prim_met, train_coupled = split_data(train)
        
        test_inds = np.arange(start, stop)
        test = shuffled.iloc[test_inds, :]
        test = test.set_index(["paired", "metaStatus"])
        test_prim_only, test_met_only, test_prim_met, test_coupled = split_data(test)
        for j in range(splits.size):
            x = opt.minimize(reg_opt.value_grad, x0 = start_params, args = (train_prim_only, train_coupled, train_prim_met, train_met_only, n-1, splits[j], m_p_corr), 
                method = "L-BFGS-B", jac = True, options={"maxiter":10000, "disp":True, "ftol":1e-04})
            runs[i,j] = reg_opt.value_grad(x.x, test_prim_only, test_coupled, test_prim_met, test_met_only, n-1, splits[j], m_p_corr)[0]
            print("split ", j, " fold: ", i)
    res = runs.sum(axis=1)
    return splits[np.argmax(res)]




def indep(dat: jnp.array) -> jnp.array:
    n = (dat.shape[1] - 1)//2
    theta = jnp.zeros((n + 1,n + 1))
    for i in range(n):
        occ = dat.at[:,2*i].get() + dat.at[:, 2*i+1].get()
        perc = jnp.sum(occ)
        if perc == 0:
            theta = theta.at[i,i].set(-120.0)
        else:
            theta = theta.at[i,i].set(jnp.log(perc/(2*dat.shape[0] - perc + 1e-10)))
    perc = jnp.sum(dat.at[:,-1].get())
    theta = theta.at[n,n].set(jnp.log(perc/(dat.shape[0] - perc + 1e-10)))
    return theta