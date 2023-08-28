import jax.numpy as jnp
import numpy as np
from jx import likelihood as ssr
from jx import vanilla as mhn
from jx.kronvec import obs_states, diagnosis_theta

def L1(theta: jnp.array, eps: float = 1e-05) -> float:
    """
    Computes the L1 penalty
    """
    theta_ = theta.copy()
    if theta.ndim == 2:
        theta_ = theta_.at[jnp.diag_indices(theta.shape[0])].set(0.)
    return jnp.sum(jnp.sqrt(theta_**2 + eps))


def L1_(theta: np.ndarray, eps: float = 1e-05) -> jnp.ndarray:
    """
    Derivative of the L1 penalty
    """
    theta_ = theta.copy()
    if theta.ndim == 2:
        theta_ = theta_.at[jnp.diag_indices(theta.shape[0])].set(0.)
    return theta_.flatten() / jnp.sqrt(theta_.flatten()**2 + eps)


def lp_prim_only(log_theta: jnp.array, fd_effects, dat: jnp.array) -> jnp.array:
    """Calculates the marginal likelihood of observing unpaired primary tumors at t_1

    Args:
        log_theta (jnp.array):      Theta matrix with logarithmic entries
        fd_effects (jnp.array):     Effects of the PT on first diagnosis
        dat (jnp.array):            Dataset of unpaired primary tumors

    Returns:
        jnp.array: log(L(\theta; Dat_prim))
    """
    score = 0.0
    n_muts = log_theta.shape[0] - 1
    log_theta_prim = log_theta.copy()
    log_theta_prim = log_theta_prim.at[:-1,-1].set(0.)
    log_theta_prim_fd = diagnosis_theta(log_theta_prim, fd_effects)
    for i in range(dat.shape[0]):
        state_prim = dat.at[i, 0:2*n_muts+1:2].get()
        n_prim = int(state_prim.sum())
        score += ssr._lp_prim_obs(log_theta_prim_fd, state_prim, n_prim)
    return score


def lp_met_only(log_theta: jnp.array, fd_effects: jnp.array, sd_effects: jnp.array, dat: jnp.array) -> jnp.array:
    """Calculates the marginal likelihood of observing an unpaired MT at second sampling

    Args:
        log_theta (jnp.array):      Theta matrix with logarithmic entries
        fd_effects (jnp.array):     Effects of PT on first diagnosis
        sd_effects (jnp.array):     Effects of MT on second diagnosis
        dat (jnp.array):            Dataset

    Returns:
        jnp.array: log(P(state))
    """
    log_theta_fd = diagnosis_theta(log_theta, fd_effects)
    log_theta_sd = diagnosis_theta(log_theta, sd_effects)
    n_mut = log_theta.shape[0] - 1
    score = 0.0
    for i in range(dat.shape[0]):
        state_met = jnp.append(dat.at[i, 1:2*n_mut+1:2].get(), 1)
        n_met = int(jnp.sum(state_met))
        score += ssr._lp_met_obs(log_theta_fd, log_theta_sd, state_met, n_met)
    return score


def lp_coupled(log_theta: jnp.array, fd_effects: jnp.array, sd_effects: jnp.array, dat: jnp.array) -> jnp.array:
    """Calculates the log likelihood score of sequential observations of PT-MT pairs

    Args:
        log_theta (jnp.array):      theta matrix with logarithmic entries
        fd_effects (jnp.array):     Effects of PT on first diagnosis
        sd_effects (jnp.array):     Effects of MT on second diagnosis
        dat (jnp.array):            Dataset

    Returns:
        jnp.array: log(L(\theta; Dat_coupled))
    """
    score = 0.0
    n_muts = log_theta.shape[0] - 1
    log_theta_fd = diagnosis_theta(log_theta, fd_effects)
    log_theta_sd = diagnosis_theta(log_theta, sd_effects)
    log_theta_prim_fd = log_theta_fd.copy()
    log_theta_prim_fd = log_theta_prim_fd.at[:-1,-1].set(-1.*fd_effects.at[-1].get())
    for i in range(dat.shape[0]):
        state_joint = dat.at[i, 0:2*n_muts+1].get()
        n_prim = int(state_joint.at[::2].get().sum())
        n_met = int(state_joint.at[1::2].get().sum() + 1)
        score += ssr._lp_coupled(log_theta_fd, log_theta_prim_fd, log_theta_sd, state_joint, n_prim, n_met)
    return score


def log_lik(params: np.array, dat_prim_only: jnp.array, dat_prim_met: jnp.array, dat_met: jnp.array, 
            dat_coupled: jnp.array, penal1: float, perc_met: float) -> np.array:
    """Calculates the negative log. likelihood 

    Args:
        params (np.array): n(n+2) dimensional array holding the parameters of the model 
        dat_prim (jnp.array): Dataset containing PT-genotypes only, where no MT was ever diagnosed
        dat_prim_met (jnp.array): Dataset containing MT-genotypes, where an MT was diagnosde but not sequenced
        dat_coupled (jnp.array): Dataset containing PT-MT pairs
        penal (float): Weight of L1-penalization
        perc_met (float): Weights to correct for sampling biases

    Returns:
        np.array: - log. likelihood
    """
    
    n_mut = (dat_prim_only.shape[1]-1)//2
    n_total = n_mut + 1
    log_theta = jnp.array(params[0:n_total**2]).reshape((n_total, n_total))
    fd_effects = jnp.array(params[n_total**2:n_total*(n_total + 1)])
    sd_effects = jnp.array(params[n_total*(n_total+1):])
    
    l1 = L1(log_theta) + L1(fd_effects) + L1(sd_effects)
    score_prim, score_coupled, score_met, score_prim_met = 0., 0., 0., 0.
    n_prim_only, n_met_only, n_coupled, n_prim_met = 0, 0, 0, 0
    
    if dat_prim_only != None:
        score_prim = lp_prim_only(log_theta, fd_effects, dat_prim_only)
        n_prim_only =  dat_prim_only.shape[0]
    
    if dat_prim_met != None:
        score_prim_met = lp_prim_only(log_theta, fd_effects, dat_prim_met)
        n_prim_met =  dat_prim_met.shape[0]
    
    if dat_met != None:
        score_met = lp_met_only(log_theta, fd_effects, sd_effects, dat_met)
        n_met_only =  dat_met.shape[0]

    if dat_coupled != None:
        score_coupled = lp_coupled(log_theta, fd_effects, sd_effects, dat_coupled)
        n_coupled = dat_coupled.shape[0]

    n_met = n_met_only + n_prim_met + n_coupled
    score = (1 - perc_met) * score_prim/n_prim_only + perc_met/n_met * (score_met + score_prim_met + score_coupled)
    # The optimizer needs np.arrays as input
    return np.array(-score + penal1 * l1)


def grad_prim_only(log_theta:jnp.array, fd_effects: jnp.array, dat: jnp.array) -> tuple:
    """Gradient of lp_prim
    
    Args:
        log_theta (jnp.array):      Theta matrix with logarithmic entries
        fd_effects (jnp.array):     Effects of mutations on first diagnosis
        dat (jnp.array):            Dataset with PTs only

    Returns:
        tuple:                      Likelihood, gradient wrt. model parameters
    """
    n_total = log_theta.shape[0]
    log_theta_fd = diagnosis_theta(log_theta, fd_effects)
    log_theta_prim_fd = log_theta_fd.at[:-1,-1].set(-1.*fd_effects.at[-1].get())
    g = jnp.zeros((n_total, n_total))
    d_fd = jnp.zeros(n_total)
    score = 0.0
    for i in range(dat.shape[0]):
        state_obs = dat.at[i, 0:2*n_total-1:2].get()
        n_prim = int(state_obs.sum())      
        s, g_, fd_ = ssr._grad_prim_obs(log_theta_prim_fd, state_obs, n_prim)
        g += g_
        d_fd += fd_
        score += s
    return score, jnp.concatenate((g.flatten(), d_fd, jnp.zeros(n_total)))


def grad_met_only(log_theta: jnp.array, fd_effects : jnp.array, sd_effects: jnp.array, dat: jnp.array) -> tuple:
    """ Gradient of lp_met

      Args:
        log_theta (jnp.array):      Theta matrix with logarithmic entries
        fd_effects (jnp.array):     Effects of mutations in the PT on the rate of first diagnosis
        sd_effects (jnp.array):     Effects of mutation in the MT on the rate of second diagnosis
        dat (jnp.array):            Dataset of MT-genotypes without PT-sequences

    Returns:
        tuple:                      Marginal Likelihood, grad wrt. model parameters
    """
    # Unpack parameters
    log_theta_fd = diagnosis_theta(log_theta, fd_effects)
    log_theta_sd = diagnosis_theta(log_theta, sd_effects)
    n_mut = log_theta.shape[0] - 1
    
    g = jnp.zeros((n_mut+1,n_mut+1), dtype=float)
    d_fd = jnp.zeros(n_mut+1)
    d_sd = jnp.zeros(n_mut+1)
    score = 0.0
    
    for i in range(dat.shape[0]):
        state = dat.at[i, 0:2*n_mut+1].get()
        state_met = jnp.append(state.at[1:2*n_mut+1:2].get(), 1)
        n_met = int(state_met.sum())
        s, g_, fd_, sd_ = ssr._grad_met_obs(log_theta_fd, log_theta_sd, state_met, n_met)
        score += s
        g += g_
        d_fd += fd_
        d_sd += sd_
    return score, jnp.concatenate((g.flatten(), d_fd, d_sd))


def grad_coupled(log_theta: jnp.array, fd_effects: jnp.array, sd_effects: jnp.array, dat: jnp.array) -> tuple[jnp.array, jnp.array]:
    """Returns the likelihood and gradients for a dataset containing coupled genotypes of PTs and MTs

    Args:
        log_theta (jnp.array):      Theta matrix with logarithmic entries
        fd_effects (jnp.array):     Effects of mutations in the PT on the rate of first diagnosis
        sd_effects (jnp.array):     Effects of mutations in the MT on the rate of second diagnosis
        dat (jnp.array):            Dataset containing coupled genotypes of PTs and MTs

    Returns:
        tuple[jnp.array, jnp.array]: likelihood, gradient wrt. model parameters
    """
    # Unpack parameters
    n_mut = log_theta.shape[0] - 1
    log_theta_fd = diagnosis_theta(log_theta, fd_effects)
    log_theta_sd = diagnosis_theta(log_theta, sd_effects)
    log_theta_prim_fd = log_theta_fd.copy()
    log_theta_prim_fd = log_theta_prim_fd.at[:-1,-1].set(-1.*fd_effects.at[-1].get())

    g = jnp.zeros((n_mut+1, n_mut+1), dtype=float)
    d_fd = jnp.zeros(n_mut+1)
    d_sd = jnp.zeros(n_mut+1)
    score = 0.0
    for i in range(dat.shape[0]):
        state = dat.at[i, 0:2*n_mut+1].get()
        n_prim = int(state.at[::2].get().sum())
        n_met = int(state.at[1::2].get().sum() + 1)      
        lik, dtheta, fd_, sd_ = ssr._g_coupled(log_theta_fd, log_theta_prim_fd, log_theta_sd, state, n_prim, n_met)
        score += lik
        g += dtheta
        d_fd += fd_
        d_sd += sd_
    return score, jnp.concatenate((g.flatten(), d_fd, d_sd)) 


def grad(params: np.array, dat_prim_only: jnp.array, dat_prim_met:jnp.array, dat_met: jnp.array, dat_coupled: jnp.array,
        penal: float, perc_met: float) -> np.array:
    """Calculates the gradient of log_lik wrt. to all log(\theta_ij) and wrt. \lambda_1

    Args:
        params (np.array):          Array of size n(n+2), holding all parameters of the model 
        dat_prim_only (jnp.array):  Dataset containing only PT genotypes, that never spawned an MT
        dat_prim_met (jnp.array):   Dataset containing only PT genotypes, that spawned an MT
        dat_met (jnp.array):        Dataset containing only MT genotypes
        dat_coupled (jnp.array):    Dataset containing coupled PT-MT pairs
        penal (float):              Weight of L1-penalization
        perc_met (float):           Weights to correct for sampling biases

    Returns:
        np.array:                   Gradient wrt. params
    """
    # Unpack parameters
    n_mut = (dat_prim_only.shape[1]-1)//2
    n_total = n_mut + 1
    log_theta = jnp.array(params[0:n_total**2]).reshape((n_total, n_total))
    fd_effects = jnp.array(params[n_total**2:n_total*(n_total + 1)])
    sd_effects = jnp.array(params[n_total*(n_total+1):])
    
    # Penalties and their derivatives
    l1_ = np.concatenate((L1_(log_theta), L1_(fd_effects), L1_(sd_effects)))
    
    g_prim, g_coupled = jnp.zeros(n_total*(n_total + 2)), jnp.zeros(n_total*(n_total + 2))
    g_met, g_prim_met = jnp.zeros(n_total*(n_total + 2)), jnp.zeros(n_total*(n_total + 2))
    
    n_prim_only, n_coupled, n_prim_met, n_prim_met = 0, 0, 0, 0
    
    if dat_prim_only != None:
        _, g_prim = grad_prim_only(log_theta, fd_effects, dat_prim_only)
        n_prim_only =  dat_prim_only.shape[0]
    
    if dat_prim_met != None:
        _, g_prim_met = grad_prim_only(log_theta, fd_effects, dat_prim_met)
        n_prim_met =  dat_prim_met.shape[0]
    
    if dat_met != None:
        _, g_met = grad_met_only(log_theta, fd_effects, sd_effects, dat_met)
        n_met_only =  dat_met.shape[0]

    if dat_coupled != None:
        _, g_coupled = grad_coupled(log_theta, fd_effects, sd_effects, dat_coupled)
        n_coupled = dat_coupled.shape[0]
    
    n_met = n_coupled + n_met_only + n_prim_met
    g = (1 - perc_met) * g_prim/n_prim_only + perc_met/n_met * (g_coupled + g_prim_met + g_met)
    return np.array(-g + penal * l1_)