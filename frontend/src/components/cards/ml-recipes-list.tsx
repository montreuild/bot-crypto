'use client';

/**
 * S9-F3-US1 — Liste des recettes ML disponibles.
 *
 * Consomme /api/ml/recipes pour afficher les recettes LightGBM disponibles
 * avec leur description, features catalog, label scheme et heads.
 *
 * Permet à l'utilisateur expert de choisir la recette à entraîner.
 *
 * Sprint 4 / ML-003 — le bouton « Entraîner » ouvre désormais un dialog
 * (`<TrainRecipeDialog>`) qui lance l'entraînement directement depuis
 * l'onglet ML au lieu de rediriger vers `/models?recipe=…`. Le lien
 * secondaire vers le registre reste accessible depuis le dialog.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Loader2, Brain, ArrowRight, CheckCircle2, XCircle } from 'lucide-react';
import { useState } from 'react';
import { cn } from '@/lib/utils';
import {
  TrainRecipeDialog,
  type TrainRecipeDialogRecipe,
} from '@/components/cards/train-recipe-dialog';

export function MLRecipesList() {
  const [trainRecipe, setTrainRecipe] = useState<TrainRecipeDialogRecipe | null>(null);
  const [trainOpen, setTrainOpen] = useState(false);
  const { data, isLoading, isError } = useQuery({
    queryKey: ['ml-recipes'],
    queryFn: api.getMLRecipes,
    refetchInterval: 60000,
  });

  const openTrainDialog = (recipe: TrainRecipeDialogRecipe) => {
    setTrainRecipe(recipe);
    setTrainOpen(true);
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Brain className="w-4 h-4 text-purple-400" />
            Recettes ML disponibles
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-5 h-5 animate-spin text-muted" />
            </div>
          ) : isError ? (
            <p className="text-xs text-red-400">Erreur de chargement</p>
          ) : !data?.recipes || data.recipes.length === 0 ? (
            <p className="text-xs text-muted">Aucune recette disponible</p>
          ) : (
            <div className="space-y-3">
              {data.recipes.map((recipe) => {
                const trainable = recipe.trainable !== false;
                // `features_catalog` est un **identifiant** de catalogue
                // (« dyn_threshold@1 »), pas la liste des features. La version
                // livrée le traitait comme un tableau : le badge affichait la
                // longueur de la chaîne (« 15 features ») et le dépliant
                // « Voir les features » appelait `.slice().map()` dessus, ce qui
                // faisait planter toute la page /ml dans l'ErrorBoundary.
                const heads: string[] = Array.isArray(recipe.heads) ? recipe.heads : [];
                const headsCount = heads.length;
                return (
                  <div
                    key={recipe.recipe}
                    className={cn(
                      'p-3 rounded-lg border transition-colors',
                      trainable
                        ? 'border-border bg-card hover:border-purple-400/50'
                        : 'border-border bg-card opacity-60',
                    )}
                  >
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm font-semibold truncate">
                            {recipe.recipe}
                          </span>
                          {trainable ? (
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                          ) : (
                            <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
                          )}
                        </div>
                        {recipe.label_scheme && (
                          <div className="text-[10px] text-muted mt-0.5">
                            Label: <span className="font-mono">{recipe.label_scheme}</span>
                          </div>
                        )}
                        {/* LAB-07 : sans ce lien, l'écran empile deux listes
                            aux noms disjoints sans dire ce qui les relie. */}
                        <div className="text-[10px] mt-0.5">
                          {recipe.used_by?.length ? (
                            <>
                              <span className="text-dim">Utilisée par </span>
                              <span className="font-mono text-cyan-400">
                                {recipe.used_by.join(', ')}
                              </span>
                            </>
                          ) : (
                            <span className="text-dim">Consommée par aucune stratégie</span>
                          )}
                        </div>
                      </div>
                      {/* ML-003 — ouvre un dialog d'entraînement direct au
                          lieu de rediriger vers `/models?recipe=…`. Le
                          utilisateur reste dans l'onglet ML, peut choisir
                          symbole/TF/bougies, suivre le polling et rafraîchir
                          la StrategyTable en cas de succès. */}
                      {trainable && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => openTrainDialog(recipe as TrainRecipeDialogRecipe)}
                        >
                          Entraîner
                          <ArrowRight className="w-3 h-3 ml-1" />
                        </Button>
                      )}
                    </div>

                    {/* Reason si non trainable */}
                    {!trainable && recipe.reason && (
                      <p className="text-[10px] text-amber-400 italic mb-2">
                        ⚠ {recipe.reason}
                      </p>
                    )}

                    {/* Catalogue de features + schéma de labels + heads */}
                    <div className="flex items-center gap-2 flex-wrap">
                      {recipe.features_catalog && (
                        <Badge variant="muted" className="text-[9px] font-mono">
                          {recipe.features_catalog}
                        </Badge>
                      )}
                      {recipe.label_scheme && (
                        <Badge variant="purple" className="text-[9px] font-mono">
                          {recipe.label_scheme}
                        </Badge>
                      )}
                      {headsCount > 0 && (
                        <Badge variant="info" className="text-[9px]">
                          {heads.join(' · ')}
                        </Badge>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ML-003 — dialog piloté par l'état local. Rendu en dehors du Card
          pour que le DialogPortal monte au niveau racine (sinon le backdrop
          est clipé par les bords arrondis du Card et le focus trapping
          casse). */}
      <TrainRecipeDialog
        recipe={trainRecipe}
        open={trainOpen}
        onOpenChange={setTrainOpen}
      />
    </>
  );
}
