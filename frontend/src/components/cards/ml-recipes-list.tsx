'use client';

/**
 * S9-F3-US1 — Liste des recettes ML disponibles.
 *
 * Consomme /api/ml/recipes pour afficher les recettes LightGBM disponibles
 * avec leur description, features catalog, label scheme et heads.
 *
 * Permet à l'utilisateur expert de choisir la recette à entraîner.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Loader2, Brain, ArrowRight, CheckCircle2, XCircle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';

export function MLRecipesList() {
  const router = useRouter();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['ml-recipes'],
    queryFn: api.getMLRecipes,
    refetchInterval: 60000,
  });

  return (
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
            {data.recipes.map((recipe: any) => {
              const trainable = recipe.trainable !== false;
              const featuresCount = recipe.features_catalog?.length ?? 0;
              const headsCount = recipe.heads?.length ?? 0;
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
                    </div>
                    {trainable && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => router.push(`/models?recipe=${encodeURIComponent(recipe.recipe)}`)}
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

                  {/* Stats features + heads */}
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="muted" className="text-[9px]">
                      {featuresCount} features
                    </Badge>
                    {headsCount > 0 && (
                      <Badge variant="info" className="text-[9px]">
                        {headsCount} heads
                      </Badge>
                    )}
                    {recipe.features_catalog && recipe.features_catalog.length > 0 && (
                      <details className="text-[10px] text-muted">
                        <summary className="cursor-pointer hover:text-foreground">
                          Voir les features
                        </summary>
                        <div className="mt-1 ml-3 flex flex-wrap gap-1">
                          {recipe.features_catalog.slice(0, 10).map((f: string) => (
                            <span key={f} className="font-mono text-[9px] text-dim bg-card-hover px-1 py-0.5 rounded">
                              {f}
                            </span>
                          ))}
                          {recipe.features_catalog.length > 10 && (
                            <span className="text-[9px] text-dim">
                              +{recipe.features_catalog.length - 10} autres
                            </span>
                          )}
                        </div>
                      </details>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
